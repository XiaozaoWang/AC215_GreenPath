from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from google.api_core.exceptions import NotFound
from google.iam.v1.policy_pb2 import Policy

import cli


@pytest.mark.parametrize("service,new,rag_override", [
    ("api", True, None), ("api", False, "https://stale-rag.run.app"), ("frontend", False, None),
])
def test_deploy_uses_model_deployment_pattern_and_preserves_access(monkeypatch, service, new, rag_override):
    monkeypatch.delenv("RAG_SERVICE_URL", raising=False)
    if rag_override:
        monkeypatch.setenv("RAG_SERVICE_URL", rag_override)
    client = Mock()
    name = f"projects/{cli.GCP_PROJECT}/locations/{cli.GCP_REGION}/services/{cli.SERVICE_NAMES[service]}"
    def lookup(*, name):
        if new and name.endswith("/" + cli.SERVICE_NAMES[service]):
            raise NotFound("new service")
        if name.endswith("/greenpath-model-service"):
            return SimpleNamespace(uri="https://model.run.app")
        if name.endswith("/greenpath-rag-service"):
            return SimpleNamespace(uri="https://rag.run.app")
        return SimpleNamespace(uri="https://upstream.run.app")
    client.get_service.side_effect = lookup
    client.create_service.return_value.result.return_value = SimpleNamespace(uri="https://new.run.app")
    client.update_service.return_value.result.return_value = SimpleNamespace(uri="https://new.run.app")
    client.get_iam_policy.return_value = Policy(version=3, etag=b"etag", bindings=[
        {"role": "roles/run.invoker", "members": ["allUsers", "allAuthenticatedUsers", "user:owner@example.com"]},
        {"role": "roles/viewer", "members": ["user:reader@example.com"]},
        {"role": "roles/run.invoker", "members": ["user:conditional@example.com"],
         "condition": {"expression": "request.time < timestamp('2030-01-01T00:00:00Z')"}},
    ])
    monkeypatch.setattr(cli.run_v2, "ServicesClient", Mock(return_value=client))
    cli.deploy(service, tag="test", model_concurrency=3)
    call = client.create_service.call_args if new else client.update_service.call_args
    config = call.kwargs["service"]
    template = config.template
    assert config.name == ("" if new else name)
    assert template.service_account == f"{cli.SERVICE_NAMES[service]}@{cli.GCP_PROJECT}.iam.gserviceaccount.com"
    assert template.revision.startswith(cli.SERVICE_NAMES[service] + "-")
    assert template.scaling.max_instance_count == 3
    assert template.max_instance_request_concurrency == (5 if service == "api" else 20)
    assert template.containers[0].ports[0].container_port == 8080
    assert template.containers[0].resources.cpu_idle
    assert not config.invoker_iam_disabled and config.traffic[0].percent == 100
    if not new:
        assert "invoker_iam_disabled" in call.kwargs["update_mask"].paths
    env = {v.name: v.value for v in template.containers[0].env}
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in env
    if service == "api":
        assert env["MODEL_MAX_CONCURRENCY"] == "3"
        assert env["MODEL_SERVICE_URL"] == "https://model.run.app"
        assert env["RAG_SERVICE_URL"] == "https://rag.run.app"
        client.get_service.assert_any_call(
            name=f"projects/{cli.GCP_PROJECT}/locations/{cli.GCP_REGION}/services/greenpath-rag-service")
    else:
        assert env["API_SERVICE_URL"] == "https://upstream.run.app"
        assert "RAG_SERVICE_URL" not in env
    access = client.set_iam_policy.call_args.kwargs["request"]
    assert access["resource"] == name and access["policy"].etag == b"etag"
    members = [m for b in access["policy"].bindings if b.role == "roles/run.invoker" for m in b.members]
    assert ("allUsers" in members) == (service == "frontend")
    assert "allAuthenticatedUsers" not in members
    assert "user:conditional@example.com" in members
    assert any(b.role == "roles/viewer" for b in access["policy"].bindings)
    if service == "api":
        assert f"serviceAccount:greenpath-frontend@{cli.GCP_PROJECT}.iam.gserviceaccount.com" in members


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
    cli.test("frontend", local=True, full=full)
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
    cli.test("api")
    token.assert_called_once_with("https://api.run.app")
    assert get.call_args.kwargs["headers"] == {"Authorization": "Bearer test-token"}
