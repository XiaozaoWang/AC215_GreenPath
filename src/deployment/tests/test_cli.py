import io
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest
from google.api_core.exceptions import NotFound
from google.iam.v1.policy_pb2 import Policy

import cli


def live_lookup(model_max=100, missing=None):
    # Fake Cloud Run lookups: upstream URLs, and the live model-service instance cap used by the API budget check
    def lookup(*, name):
        if missing and name.endswith("/" + missing):
            raise NotFound("new service")
        if name.endswith("/greenpath-model-service"):
            return SimpleNamespace(uri="https://model.run.app", template=SimpleNamespace(scaling=SimpleNamespace(max_instance_count=model_max)))
        if name.endswith("/greenpath-rag-service"):
            return SimpleNamespace(uri="https://rag.run.app")
        return SimpleNamespace(uri="https://api.run.app")
    return lookup


def policy():
    return Policy(version=3, etag=b"etag", bindings=[
        {"role": "roles/run.invoker", "members": ["allUsers", "allAuthenticatedUsers", "user:owner@example.com"]},
        {"role": "roles/viewer", "members": ["user:reader@example.com"]},
        {"role": "roles/run.invoker", "members": ["user:conditional@example.com"],
         "condition": {"expression": "request.time < timestamp('2030-01-01T00:00:00Z')"}},
    ])


@pytest.mark.parametrize("service,new", [("model", True), ("model", False), ("api", False), ("frontend", False)])
def test_deploy_settings_and_access(monkeypatch, service, new):
    client = Mock()
    client.get_service.side_effect = live_lookup(missing=cli.SERVICES[service]["name"] if new else None)
    client.create_service.return_value.result.return_value = SimpleNamespace(uri="https://new.run.app")
    client.update_service.return_value.result.return_value = SimpleNamespace(uri="https://new.run.app")
    client.get_iam_policy.return_value = policy()
    monkeypatch.setattr(cli.run_v2, "ServicesClient", Mock(return_value=client))
    cli.deploy(service, tag="test")

    call = client.create_service.call_args if new else client.update_service.call_args
    config, s = call.kwargs["service"], cli.SERVICES[service]
    template, container = config.template, config.template.containers[0]
    assert config.name == ("" if new else cli.full_name(service))
    assert template.service_account == f"{s['name']}@{cli.GCP_PROJECT}.iam.gserviceaccount.com"
    assert template.revision.startswith(s["name"] + "-")
    assert container.image.endswith(f"/greenpath/{s['name']}:test")
    # the numbers that must not get mixed up between services
    expected = {"model": (100, 1, "4", "4Gi"), "api": (1, 20, "2", "2Gi"), "frontend": (3, 20, "1", "512Mi")}[service]
    assert (template.scaling.max_instance_count, template.max_instance_request_concurrency,
            container.resources.limits["cpu"], container.resources.limits["memory"]) == expected
    assert config.scaling.max_instance_count == expected[0]
    assert container.resources.cpu_idle and container.resources.startup_cpu_boost
    assert container.ports[0].container_port == 8080
    assert not config.invoker_iam_disabled and config.traffic[0].percent == 100
    if not new:
        assert {"invoker_iam_disabled", "scaling", "template"} <= set(call.kwargs["update_mask"].paths)

    env = {v.name: v.value for v in container.env}
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in env
    if service == "model":
        assert env == {"GREENPATH_BUCKET": cli.BUCKET, "DATASETS": "1026_Full_data", "TORCH_THREADS": "4"}
    elif service == "api":
        assert env["MODEL_MAX_CONCURRENCY"] == "100" and env["MODEL_SERVICE_URL"] == "https://model.run.app"
        assert env["RAG_SERVICE_URL"] == "https://rag.run.app" and env["LLM_PROVIDER"] == "gemini"
    else:
        assert env == {"API_SERVICE_URL": "https://api.run.app"}

    # who may call it: frontend public, api only the frontend, model only the api; other bindings untouched
    access = client.set_iam_policy.call_args.kwargs["request"]
    assert access["resource"] == cli.full_name(service) and access["policy"].etag == b"etag"
    members = [m for b in access["policy"].bindings if b.role == "roles/run.invoker" for m in b.members]
    caller = {"model": "greenpath-api-service", "api": "greenpath-frontend"}.get(service)
    assert ("allUsers" in members) == (service == "frontend")
    assert "allAuthenticatedUsers" not in members and "user:conditional@example.com" in members
    assert any(b.role == "roles/viewer" for b in access["policy"].bindings)
    if caller:
        assert f"serviceAccount:{caller}@{cli.GCP_PROJECT}.iam.gserviceaccount.com" in members


def test_api_budget_must_fit_model_instances(monkeypatch):
    # model-service still capped at 30, API wants 100 calls at once: refuse before touching anything
    client = Mock()
    client.get_service.side_effect = live_lookup(model_max=30)
    monkeypatch.setattr(cli.run_v2, "ServicesClient", Mock(return_value=client))
    with pytest.raises(SystemExit, match="Deploy the model first"):
        cli.deploy("api")
    client.update_service.assert_not_called()
    client.create_service.assert_not_called()
    # a budget that fits is fine
    client.get_service.side_effect = live_lookup(model_max=30)
    client.update_service.return_value.result.return_value = SimpleNamespace(uri="https://new.run.app")
    client.get_iam_policy.return_value = policy()
    cli.deploy("api", model_concurrency=30)
    env = {v.name: v.value for v in client.update_service.call_args.kwargs["service"].template.containers[0].env}
    assert env["MODEL_MAX_CONCURRENCY"] == "30"


def test_model_parity_test_passes_and_fails(monkeypatch):
    targets = ["tree_canopy", "water_access"]
    cells = [f"area_8a2a{i:011x}" for i in range(60)]
    expected = pd.DataFrame({"cell_id": cells, "tree_canopy": 0.5, "water_access": 0.25})
    buf = io.BytesIO(); expected.to_parquet(buf)
    fs = Mock(); fs.cat.return_value = buf.getvalue()
    monkeypatch.setattr(cli.requests, "get", Mock(return_value=Mock(json=Mock(return_value={
        "run_id": "r", "training_id": "t", "targets": targets, "cells": 60}))))
    def answer(offset):
        def post(url, json, headers, timeout):
            rows = [{"h3_index": h, "status": "ok", "scores": {"tree_canopy": 0.5 + offset, "water_access": 0.25}} for h in json["h3_indices"]]
            return Mock(json=Mock(return_value={"results": rows}))
        return post
    monkeypatch.setattr(cli.requests, "post", answer(0.002))
    cli.test_model(fs, local=True)                     # within tolerance
    monkeypatch.setattr(cli.requests, "post", answer(0.05))
    with pytest.raises(SystemExit):
        cli.test_model(fs, local=True)                 # off by 0.05


@pytest.mark.parametrize("full", [False, True])
def test_local_smoke_keeps_expensive_scoring_optional(monkeypatch, full):
    monkeypatch.setattr(cli.run_v2, "ServicesClient", Mock(side_effect=AssertionError("local test must not contact Cloud Run")))
    response = Mock()
    response.json.return_value = {"h3_index": ["a"]}
    monkeypatch.setattr(cli.requests, "get", Mock(return_value=response))
    score = Mock()
    score.json.return_value = {"cells": [{"h3_index": "a", "value": 0}], "weights": dict.fromkeys(range(15), 0),
                              "agent": {"used_fallback": False}, "timings_ms": {}}
    post = Mock(return_value=score)
    monkeypatch.setattr(cli.requests, "post", post)
    cli.test_app("frontend", local=True, full=full)
    assert post.call_count == int(full)


def test_private_api_smoke_uses_id_token(monkeypatch):
    client = Mock()
    client.get_service.return_value.uri = "https://api.run.app"
    monkeypatch.setattr(cli.run_v2, "ServicesClient", Mock(return_value=client))
    token = Mock(return_value="test-token")
    monkeypatch.setattr(cli, "id_token", token)
    region = Mock()
    region.json.return_value = {"h3_index": ["a"]}
    get = Mock(side_effect=[Mock(status_code=403), Mock(), region])
    monkeypatch.setattr(cli.requests, "get", get)
    cli.test_app("api")
    token.assert_called_once_with("https://api.run.app")
    assert get.call_args.kwargs["headers"] == {"Authorization": "Bearer test-token"}
