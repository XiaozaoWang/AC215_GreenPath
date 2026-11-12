"""
Deploy and test API Service and Frontend, following model-deployment/cli.py.
Build/push each image on the host with its docker-build-push.sh first.
This tool runs locally and uses the operator's ADC; runtime services use their own service accounts.
"""
import argparse
import os
import time

import google.auth
import google.auth.transport.requests
import google.oauth2.id_token
import requests
from google.api_core.exceptions import NotFound
from google.cloud import run_v2
from google.protobuf.field_mask_pb2 import FieldMask

# docker-shell.sh passes .env, just like model-deployment.
GCP_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "organic-victory-508017-v9")
GCP_REGION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
BUCKET = os.getenv("GREENPATH_BUCKET", "ac215-notebook")
SERVICE_NAMES = {"api": "greenpath-api-service", "frontend": "greenpath-frontend"}
LOCAL_URLS = {"api": "http://host.docker.internal:9000", "frontend": "http://host.docker.internal:3000"}


# Keep API private and allow the frontend identity; make only Frontend public.
def set_access(client, name, public):
    policy = client.get_iam_policy(request={"resource": name, "options": {"requested_policy_version": 3}})
    # Preserve other roles, conditions, and the policy etag when changing Invoker access.
    for binding in list(policy.bindings):
        if binding.role == "roles/run.invoker":
            binding.members[:] = [m for m in binding.members if m not in ("allUsers", "allAuthenticatedUsers")]
            if not binding.members:
                policy.bindings.remove(binding)
    member = "allUsers" if public else f"serviceAccount:greenpath-frontend@{GCP_PROJECT}.iam.gserviceaccount.com"
    binding = next((b for b in policy.bindings if b.role == "roles/run.invoker" and not b.condition.expression), None)
    if binding is None:
        binding = policy.bindings.add(role="roles/run.invoker")
    if member not in binding.members:
        binding.members.append(member)
    client.set_iam_policy(request={"resource": name, "policy": policy})


# --deploy: create/update one service from an image already published to Artifact Registry.
def deploy(service, tag="latest", model_concurrency=3):
    client = run_v2.ServicesClient()
    parent = f"projects/{GCP_PROJECT}/locations/{GCP_REGION}"
    service_name = SERVICE_NAMES[service]
    name = f"{parent}/services/{service_name}"
    if service == "frontend":
        env = {"API_SERVICE_URL": client.get_service(name=f"{parent}/services/{SERVICE_NAMES['api']}").uri}
    else:
        # Discover upstream URLs only; never change their deployments or permissions.
        model_url = client.get_service(name=f"{parent}/services/greenpath-model-service").uri
        rag_url = client.get_service(name=f"{parent}/services/greenpath-rag-service").uri
        env = {"GCP_PROJECT": GCP_PROJECT, "GREENPATH_BUCKET": BUCKET,
               "GREENPATH_DATASET": os.getenv("GREENPATH_DATASET", "1026_Full_data"),
               "MODEL_SERVICE_URL": model_url, "MODEL_MAX_CONCURRENCY": str(model_concurrency),
               "RAG_SERVICE_URL": rag_url,
               "LLM_PROVIDER": "gemini", "GEMINI_MODEL": os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite"),
               "GEMINI_LOCATION": os.getenv("GEMINI_LOCATION", "global")}

    config = run_v2.Service(
        ingress="INGRESS_TRAFFIC_ALL", invoker_iam_disabled=False,
        template=run_v2.RevisionTemplate(
            # A fresh revision also picks up a new image when the tag is still :latest.
            revision=f"{service_name}-{time.strftime('%Y%m%d-%H%M%S')}",
            service_account=f"{service_name}@{GCP_PROJECT}.iam.gserviceaccount.com",
            scaling=run_v2.RevisionScaling(min_instance_count=0, max_instance_count=3),
            max_instance_request_concurrency=5 if service == "api" else 20,
            timeout={"seconds": 900 if service == "api" else 1000},
            containers=[run_v2.Container(
                image=f"{GCP_REGION}-docker.pkg.dev/{GCP_PROJECT}/greenpath/{service_name}:{tag}",
                ports=[run_v2.ContainerPort(container_port=8080)],
                resources=run_v2.ResourceRequirements(
                    limits={"cpu": "1", "memory": "1Gi" if service == "api" else "512Mi"},
                    cpu_idle=True, startup_cpu_boost=True),
                env=[run_v2.EnvVar(name=k, value=v) for k, v in env.items()],
            )],
        ),
        traffic=[run_v2.TrafficTarget(type_="TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST", percent=100)],
    )
    try:
        client.get_service(name=name)
        config.name = name
        # Include the false IAM flag explicitly, so updates cannot leave auth checks disabled.
        operation = client.update_service(service=config, update_mask=FieldMask(
            paths=["template", "traffic", "ingress", "invoker_iam_disabled"]))
        print(f"updating {service_name} ...")
    except NotFound:
        operation = client.create_service(parent=parent, service=config, service_id=service_name)
        print(f"creating {service_name} ...")
    result = operation.result()
    set_access(client, name, public=service == "frontend")
    print("deployed:", result.uri)
    print(f"next: python cli.py --test --service {service}")


# Use the operator's user ADC token, or an audience-bound token for service-account credentials.
def id_token(audience):
    creds, _ = google.auth.default()
    request = google.auth.transport.requests.Request()
    creds.refresh(request)
    return getattr(creds, "id_token", None) or google.oauth2.id_token.fetch_id_token(request, audience)


# --test: quick endpoint checks; --full opts into real Gemini and full-region predictions.
def test(service, local=False, full=False):
    headers = {}
    if local:
        url = LOCAL_URLS[service]
    else:
        name = f"projects/{GCP_PROJECT}/locations/{GCP_REGION}/services/{SERVICE_NAMES[service]}"
        url = run_v2.ServicesClient().get_service(name=name).uri
        if service == "api":
            headers = {"Authorization": f"Bearer {id_token(url)}"}
            assert requests.get(url, timeout=60).status_code in (401, 403), "API must remain private"
    requests.get(url, headers=headers, timeout=120).raise_for_status()
    prefix = "" if service == "api" else "/api"
    response = requests.get(f"{url}{prefix}/regions/city_ma_boston_big", headers=headers, timeout=120)
    response.raise_for_status()
    ids = response.json()["h3_index"]
    assert ids, "Empty Boston region"
    print(f"{service}: root and {len(ids)} Boston cells passed", flush=True)
    if full:
        response = requests.post(f"{url}{prefix}/score", headers=headers, timeout=960,
                                 json={"text": "A shady scenic ride by the water, avoid busy roads",
                                       "region_id": "city_ma_boston_big"})
        response.raise_for_status()
        result = response.json()
        assert [c["h3_index"] for c in result["cells"]] == ids
        assert len(result["weights"]) == 15 and "hilly_terrain" not in result["weights"]
        assert any(c["value"] is not None for c in result["cells"]), "No usable predictions"
        assert not result["agent"]["used_fallback"], "Gemini fell back"
        print(f"{service}: full scoring passed; timings_ms={result['timings_ms']}")


def main():
    p = argparse.ArgumentParser(description="GreenPath app deployment")
    p.add_argument("--deploy", action="store_true", help="deploy an already-published image to Cloud Run")
    p.add_argument("--test", action="store_true", help="smoke-test API or Frontend")
    p.add_argument("--service", choices=SERVICE_NAMES, default="api")
    p.add_argument("--tag", default=os.getenv("APP_IMAGE_TAG", "latest"))
    p.add_argument("--model-concurrency", type=int, choices=range(1, 31), default=3)
    p.add_argument("--local", action="store_true", help="with --test: use the local Docker service")
    p.add_argument("--full", action="store_true", help="with --test: include real full-region scoring")
    a = p.parse_args()
    if a.deploy:
        deploy(a.service, a.tag, a.model_concurrency)
    if a.test:
        test(a.service, a.local, a.full)


if __name__ == "__main__":
    main()
