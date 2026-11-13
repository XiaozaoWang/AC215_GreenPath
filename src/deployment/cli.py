"""
Deploy GreenPath to Cloud Run and check that it works. One tool for the three services we deploy ourselves:
    model      model-service, private (only the API may call it)
    api        api-service, private (only the frontend may call it)
    frontend   the web app, public
RAG is deployed on its own (src/rag-service/deploy).

Run it inside docker-shell.sh. Push the image first with docker-build-push.sh in the service's own folder.
    python cli.py --prepare                              copy the trained model into model-service/artifacts/
    python cli.py --deploy --service model|api|frontend
    python cli.py --test   --service model|api|frontend  add --local to test the local container, --full to really score
"""
import argparse
import io
import json
import os
import shutil
import sys
import time

import gcsfs
import google.auth
import google.auth.transport.requests
import google.oauth2.id_token
import pandas as pd
import requests
from google.api_core.exceptions import NotFound
from google.cloud import run_v2
from google.protobuf.field_mask_pb2 import FieldMask

# Project, region and bucket come from .env (docker-shell.sh passes it in with --env-file)
GCP_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "organic-victory-508017-v9")
GCP_REGION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
BUCKET = os.getenv("GREENPATH_BUCKET", "ac215-notebook")
PARENT = f"projects/{GCP_PROJECT}/locations/{GCP_REGION}"

# Which trained model goes into model-service: gs://<bucket>/results/<TRAINING_ID>/final/<RUN_ID>/
TRAINING_ID = "full_v2"
RUN_ID = "dinov2_s-linear-seed42-ebcc811f"     # "latest" = the run recorded in latest.json
# Areas the model can score: the datasets the collector downloaded tiles for. To add a city, add its dataset and redeploy the model
DATASETS = ["1026_Full_data"]
ARTIFACTS = "../model-service/artifacts"                       # --prepare puts the model here, the image build copies it in
NETWORK_PY = "../model-training/package/trainer/network.py"    # same model code as training, so preprocessing matches
N_TEST = 50          # --test --service model checks this many validation cells
TOLERANCE = 0.01     # training ran in float16 on a GPU, the service runs float32 on CPU, so scores differ a tiny bit

# Everything that differs between the three services
#   invoker: who may call it, "allUsers" means public, otherwise the service account of the caller
#   model: 1 request per instance (one batch uses all 4 CPUs), up to 100 instances so the API budget of 100 calls always fits
#   api: a single instance, so all users share one budget of MODEL_MAX_CONCURRENCY model calls; 20 requests at a time
SERVICES = {
    "model": dict(name="greenpath-model-service", cpu="4", memory="4Gi", max_instances=100, concurrency=1, timeout=300,
                  invoker="greenpath-api-service", local_url="http://host.docker.internal:8080"),
    "api": dict(name="greenpath-api-service", cpu="2", memory="2Gi", max_instances=1, concurrency=20, timeout=900,
                invoker="greenpath-frontend", local_url="http://host.docker.internal:9000"),
    "frontend": dict(name="greenpath-frontend", cpu="1", memory="512Mi", max_instances=3, concurrency=20, timeout=1000,
                     invoker="allUsers", local_url="http://host.docker.internal:3000"),
}
UPDATE_PATHS = ["template", "traffic", "ingress", "invoker_iam_disabled", "scaling"]


def full_name(service):
    return f"{PARENT}/services/{SERVICES[service]['name']}"


def account(name):
    return f"{name}@{GCP_PROJECT}.iam.gserviceaccount.com"


# --prepare: copy the model we want to deploy into model-service/artifacts/
def prepare(fs):
    run_id = RUN_ID
    if run_id == "latest":
        run_id = json.loads(fs.cat(f"{BUCKET}/results/{TRAINING_ID}/final/latest.json"))["run_id"]
    src = f"{BUCKET}/results/{TRAINING_ID}/final/{run_id}"

    # weights and model info come from the bucket, network.py from the training code
    shutil.rmtree(ARTIFACTS, ignore_errors=True)
    os.makedirs(ARTIFACTS)
    for name in ["best_model.pt", "config.json"]:
        fs.get(f"{src}/{name}", f"{ARTIFACTS}/{name}")
    shutil.copy(NETWORK_PY, f"{ARTIFACTS}/network.py")

    cfg = json.load(open(f"{ARTIFACTS}/config.json"))
    print(f"model: gs://{src}/")
    print(f"val MAE {cfg['val_mae']:.4f} · acc ±0.10 {cfg['val_acc10']:.3f} · {len(cfg['targets'])} scores · best epoch {cfg['best_epoch']}")
    print("next: in model-service/, sh docker-shell.sh to try it locally, or sh docker-build-push.sh to push it")


# Environment variables for each service. Upstream URLs are looked up from Cloud Run, never hard-coded
def env_for(service, client, model_concurrency):
    if service == "model":
        return {"GREENPATH_BUCKET": BUCKET, "DATASETS": ",".join(DATASETS), "TORCH_THREADS": SERVICES["model"]["cpu"]}
    if service == "frontend":
        return {"API_SERVICE_URL": client.get_service(name=full_name("api")).uri}
    model = client.get_service(name=full_name("model"))
    # The API sends at most this many model calls at once. Each call takes one model instance, so the total has to fit
    budget = model_concurrency * SERVICES["api"]["max_instances"]
    if budget > model.template.scaling.max_instance_count:
        sys.exit(f"API would send up to {budget} model calls at once but model-service allows only "
                 f"{model.template.scaling.max_instance_count} instances. Deploy the model first or lower --model-concurrency.")
    return {"GCP_PROJECT": GCP_PROJECT, "GREENPATH_BUCKET": BUCKET,
            "GREENPATH_DATASET": os.getenv("GREENPATH_DATASET", "1026_Full_data"),
            "MODEL_SERVICE_URL": model.uri, "MODEL_MAX_CONCURRENCY": str(model_concurrency),
            "RAG_SERVICE_URL": client.get_service(name=f"{PARENT}/services/greenpath-rag-service").uri,
            "LLM_PROVIDER": "gemini", "GEMINI_MODEL": os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite"),
            "GEMINI_LOCATION": os.getenv("GEMINI_LOCATION", "global")}


# The Cloud Run settings for one service, built from SERVICES
def build_service(service, tag, env):
    s = SERVICES[service]
    return run_v2.Service(
        # the URL is reachable, but every call is checked against the invoker list (set_access)
        ingress="INGRESS_TRAFFIC_ALL", invoker_iam_disabled=False,
        # service-level cap, same as the revision cap, so the console shows the real limit
        scaling=run_v2.ServiceScaling(min_instance_count=0, max_instance_count=s["max_instances"]),
        template=run_v2.RevisionTemplate(
            # a new revision name on every deploy, so :latest is pulled again and old revisions stay for rollback
            revision=f"{s['name']}-{time.strftime('%Y%m%d-%H%M%S')}",
            service_account=account(s["name"]),
            # 0 instances when idle, so no cost, but the next request waits for a cold start
            scaling=run_v2.RevisionScaling(min_instance_count=0, max_instance_count=s["max_instances"]),
            max_instance_request_concurrency=s["concurrency"],
            timeout={"seconds": s["timeout"]},
            containers=[run_v2.Container(
                image=f"{GCP_REGION}-docker.pkg.dev/{GCP_PROJECT}/greenpath/{s['name']}:{tag}",
                ports=[run_v2.ContainerPort(container_port=8080)],
                # cpu_idle: CPU (and billing) only while a request is running; startup boost: extra CPU for faster cold starts
                resources=run_v2.ResourceRequirements(limits={"cpu": s["cpu"], "memory": s["memory"]},
                                                      cpu_idle=True, startup_cpu_boost=True),
                env=[run_v2.EnvVar(name=k, value=v) for k, v in env.items()],
            )],
        ),
        traffic=[run_v2.TrafficTarget(type_="TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST", percent=100)],
    )


# Who may call the service. Removes allUsers / allAuthenticatedUsers unless the service is public, keeps every other binding
def set_access(client, name, invoker):
    policy = client.get_iam_policy(request={"resource": name, "options": {"requested_policy_version": 3}})
    for binding in list(policy.bindings):
        if binding.role == "roles/run.invoker":
            binding.members[:] = [m for m in binding.members if m not in ("allUsers", "allAuthenticatedUsers")]
            if not binding.members:
                policy.bindings.remove(binding)
    member = "allUsers" if invoker == "allUsers" else f"serviceAccount:{account(invoker)}"
    binding = next((b for b in policy.bindings if b.role == "roles/run.invoker" and not b.condition.expression), None)
    if binding is None:
        binding = policy.bindings.add(role="roles/run.invoker")
    if member not in binding.members:
        binding.members.append(member)
    client.set_iam_policy(request={"resource": name, "policy": policy})


# --deploy: create or update one service from an image already pushed to Artifact Registry
def deploy(service, tag="latest", model_concurrency=100):
    client = run_v2.ServicesClient()
    name = full_name(service)
    config = build_service(service, tag, env_for(service, client, model_concurrency))
    try:
        client.get_service(name=name)
        config.name = name
        operation = client.update_service(service=config, update_mask=FieldMask(paths=UPDATE_PATHS))
        print(f"updating {SERVICES[service]['name']} ...")
    except NotFound:
        operation = client.create_service(parent=PARENT, service=config, service_id=SERVICES[service]["name"])
        print(f"creating {SERVICES[service]['name']} ...")
    result = operation.result()
    set_access(client, name, SERVICES[service]["invoker"])
    print("deployed:", result.uri)
    print(f"next: python cli.py --test --service {service}")


# An ID token for calling a private service. Your gcloud login already carries one; a service account asks for one
def id_token(audience):
    creds, _ = google.auth.default()
    request = google.auth.transport.requests.Request()
    creds.refresh(request)
    return getattr(creds, "id_token", None) or google.oauth2.id_token.fetch_id_token(request, audience)


def service_url(service, local):
    if local:
        return SERVICES[service]["local_url"], {}
    url = run_v2.ServicesClient().get_service(name=full_name(service)).uri
    return url, ({} if service == "frontend" else {"Authorization": f"Bearer {id_token(url)}"})


# --test --service model: feature parity check. The service has to give the same scores as training did for 50 validation cells
def test_model(fs, local):
    url, headers = service_url("model", local)
    health = requests.get(f"{url}/health", headers=headers, timeout=300)
    health.raise_for_status()
    h = health.json()
    print(f"model {h['run_id']} (training set {h['training_id']}) · {h['cells']:,} cells can be scored")

    expected = pd.read_parquet(io.BytesIO(fs.cat(f"{BUCKET}/results/{h['training_id']}/final/{h['run_id']}/predictions.parquet")))
    expected = expected.sample(N_TEST, random_state=0)
    h3 = expected.cell_id.str.rsplit("_", n=1).str[1]      # cell_id is <area>_<h3>
    r = requests.post(f"{url}/predict", json={"h3_indices": h3.tolist()}, headers=headers, timeout=300)
    r.raise_for_status()
    got = pd.DataFrame([x["scores"] or {} for x in r.json()["results"]], index=expected.index, columns=h["targets"])

    # per cell, the largest difference over the 15 scores; a missing score counts as a mismatch
    diff = (got - expected[h["targets"]]).abs().max(axis=1)
    bad = diff.isna() | (diff > TOLERANCE)
    print(f"{(~bad).sum()}/{len(diff)} cells match the training-time predictions (largest difference {diff.max():.4f}, tolerance {TOLERANCE})")
    if bad.any():
        print("FAIL, mismatched cells:", expected.cell_id[bad].tolist()[:10])
        sys.exit(1)
    print("PASS")


# --test --service api|frontend: the API must stay private, Boston cells load; --full also scores Boston for real (Gemini + model)
def test_app(service, local=False, full=False):
    url, headers = service_url(service, local)
    if service == "api" and not local:
        assert requests.get(url, timeout=60).status_code in (401, 403), "API must remain private"
    requests.get(url, headers=headers, timeout=120).raise_for_status()
    prefix = "" if service == "api" else "/api"      # the frontend forwards /api/* to the API
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
    p = argparse.ArgumentParser(description="GreenPath deployment")
    p.add_argument("--prepare", action="store_true", help="copy the trained model into model-service/artifacts/")
    p.add_argument("--deploy", action="store_true", help="deploy an image that is already pushed")
    p.add_argument("--test", action="store_true", help="check the deployed (or --local) service")
    p.add_argument("--service", choices=SERVICES)
    p.add_argument("--tag", default=os.getenv("APP_IMAGE_TAG", "latest"), help="image tag, default latest")
    p.add_argument("--model-concurrency", type=int, choices=range(1, 101), default=100,
                   help="api only: how many model calls the API sends at once (1-100)")
    p.add_argument("--local", action="store_true", help="with --test: test the local container instead of Cloud Run")
    p.add_argument("--full", action="store_true", help="with --test (api, frontend): also score Boston for real")
    a = p.parse_args()
    if (a.deploy or a.test) and not a.service:
        p.error("--deploy and --test need --service model|api|frontend")

    fs = gcsfs.GCSFileSystem(token="google_default") if a.prepare or (a.test and a.service == "model") else None
    if a.prepare:
        prepare(fs)
    if a.deploy:
        deploy(a.service, a.tag, a.model_concurrency)
    if a.test:
        test_model(fs, a.local) if a.service == "model" else test_app(a.service, a.local, a.full)


if __name__ == "__main__":
    main()
