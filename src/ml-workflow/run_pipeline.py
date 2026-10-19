"""Run four container jobs, verify their artifacts, then start and check the app.

This host launcher uses only Python's standard library plus the project's standard-
library artifact helper. No host ML packages or research environment are required.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/model-deploy"))
sys.path.insert(0, str(ROOT / "tools"))
from greenpath_model.artifact import digest, verify, write_json
from artifact_catalog import atomic_json, refresh_runs, register_bundle
from runtime_environment import environment


def parse_status(text):
    try:
        value = json.loads(text)
        return value if isinstance(value, list) else [value]
    except json.JSONDecodeError:
        return [json.loads(line) for line in text.splitlines() if line.strip()]


def validate_run_id(value):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,55}", value):
        raise ValueError("Run ID must contain 1-56 lowercase letters, digits or hyphens")
    return value


def check_ports(ports, environment):
    owned = subprocess.check_output(["docker", "ps", "--filter", "label=com.docker.compose.project=greenpath-pipeline-app", "--format", "{{.Ports}}"], env=environment, text=True)
    owned_ports = {int(x) for x in re.findall(r":(\d+)->", owned)}
    if len(set(ports)) != len(ports):
        raise ValueError("API and frontend ports must differ")
    for port in ports:
        if not 1024 <= port <= 65535:
            raise ValueError("Use an unprivileged TCP port in [1024,65535]")
        if port in owned_ports:
            continue
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError as error:
                raise ValueError("Port %d is occupied by another process; choose --api-port/--frontend-port before running ML jobs." % port) from error


def execute(command, environment, log_path):
    print("Running: " + " ".join(command), flush=True)
    with Path(log_path).open("w") as log:
        process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in process.stdout:
            log.write(line); log.flush(); print(line, end="", flush=True)
        code = process.wait()
    if code:
        raise RuntimeError("Command failed with exit code %s; see %s" % (code, log_path))


def verify_outputs(run_dir):
    formats = {"prepared": "greenpath-prepared-data-v1", "embeddings": "greenpath-embeddings-v1",
               "model": "greenpath-ridge-v1", "predictions": "greenpath-predictions-v1", "serving": "greenpath-region-v1"}
    manifests = {name: verify(run_dir / name, fmt) for name, fmt in formats.items()}
    prepared = json.loads((run_dir / "prepared/preparation.json").read_text())
    encoded = json.loads((run_dir / "embeddings/embedding.json").read_text())
    model = json.loads((run_dir / "model/model.json").read_text())
    prediction = json.loads((run_dir / "predictions/prediction.json").read_text())
    serving = manifests["serving"]
    count = sum(prepared["counts"].values())
    if encoded["n_cells"] != count or prediction["n_cells"] != count or model["training"]["n_fit"] != prepared["counts"]["train"] + prepared["counts"]["val"]:
        raise ValueError("Pipeline stages disagree on dataset size")
    if prediction["model_manifest_sha256"] != digest(run_dir / "model/manifest.json") or serving["model_version"] != model["model_version"]:
        raise ValueError("Prediction or serving model identity differs")
    return {"counts": prepared["counts"], "dataset_version": prepared["dataset_version"], "n_cells": count,
            "model_version": model["model_version"], "serving_version": serving["artifact_version"],
            "fresh_encoding": True, "artifact_checksums_verified": True,
            "reload_validation": json.loads((run_dir / "model/validation.json").read_text())}


def check_api(port, run_dir, expected):
    base = "http://127.0.0.1:%d" % port
    with urllib.request.urlopen(base + "/capabilities?version_id=" + expected["serving_version"], timeout=15) as response:
        meta = json.load(response)
    request = {"text": "A quiet ride with shade, avoid traffic", "region_id": meta["default_region_id"], "version_id": expected["serving_version"]}
    req = urllib.request.Request(base + "/score", data=json.dumps(request).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as response:
        result = json.load(response)
    if len(result["features"]) != expected["n_cells"] or result["model_version"] != expected["model_version"] or result["prediction_source"] != "batch_inference_reloaded_model":
        raise ValueError("The app is not serving this run's predictions")
    source_ids = {r["cell_id"] for r in json.loads((run_dir / "predictions/scores.json").read_text())}
    if {f["id"] for f in result["features"]} != source_ids:
        raise ValueError("API geographic IDs differ from the generated prediction IDs")
    write_json(run_dir / "capabilities.json", meta)
    write_json(run_dir / "sample-request.json", request)
    write_json(run_dir / "sample-response.json", result)
    return {"verified": True, "n_cells": len(result["features"]), "api_url": base, "prediction_source": result["prediction_source"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["smoke", "full"], default="smoke")
    parser.add_argument("--run-id")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--no-app", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--source-dir", type=Path, help="Use a local immutable source snapshot instead of fetching the source from GCS")
    parser.add_argument("--source-version")
    parser.add_argument("--source-uri")
    parser.add_argument("--catalog-label")
    parser.add_argument("--api-port", type=int, default=9000)
    parser.add_argument("--frontend-port", type=int, default=3000)
    args = parser.parse_args()
    run_id = validate_run_id(args.run_id or (datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]))
    runs = ROOT / ".artifacts/pipeline/runs"
    cache = ROOT / ".artifacts/pipeline/cache"
    run_dir = runs / run_id
    if run_dir.exists():
        raise ValueError("Run directory exists; choose a new run ID")
    env = environment(ROOT)
    env.update({"PIPELINE_RUN_ID": run_id, "PIPELINE_MODE": args.mode, "PIPELINE_OFFLINE": "1" if args.offline else "0",
                "PIPELINE_RUNS_DIR": str(runs), "PIPELINE_CACHE_DIR": str(cache),
                "LOCAL_UID": str(os.getuid()) if hasattr(os, "getuid") else "1000",
                "LOCAL_GID": str(os.getgid()) if hasattr(os, "getgid") else "1000"})
    if args.source_dir:
        source = args.source_dir.resolve()
        manifest = verify(source, "greenpath-processed-snapshot-v1")
        if args.source_version and args.source_version != manifest["artifact_version"]:
            raise ValueError("Local source identity differs from --source-version")
        env["PIPELINE_SOURCE_VERSION"] = manifest["artifact_version"]
        env["PIPELINE_LOCAL_SOURCE"] = str(source)
    elif args.source_version:
        env["PIPELINE_SOURCE_VERSION"] = args.source_version
    if args.source_uri:
        env["PIPELINE_SOURCE_URI"] = args.source_uri
    if not args.no_app:
        check_ports([args.api_port, args.frontend_port], env)
    project = "gp-ml-" + run_id
    command = ["docker", "compose", "-p", project, "-f", "docker-compose.ml.yml"]
    if args.source_dir:
        command += ["-f", "docker-compose.ml.local.yml"]
    if not args.offline:
        adc = Path(env.get("ADC_FILE") or str(Path.home() / ".config/gcloud/application_default_credentials.json")).expanduser()
        if not adc.is_file():
            raise ValueError("Authorized ADC is required. Set ADC_FILE or use --offline after caches have been prepared.")
        env["ADC_FILE"] = str(adc.resolve())
        command += ["-f", "docker-compose.ml.gcp.yml"]
    cache.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True)
    logs = run_dir / "logs"; logs.mkdir()
    started = time.time()
    summary = {"run_id": run_id, "mode": args.mode, "ml_compose_project": project, "success": False,
               "started_at": datetime.now(timezone.utc).isoformat(), "offline": args.offline,
               "source_input": {"kind": "local_snapshot" if args.source_dir else "gcs_snapshot", "version": env.get("PIPELINE_SOURCE_VERSION", "boston-processed-v1")}}
    summary["git_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    code_paths = ["src/data-processor/greenpath_data/dataset.py", "src/feature-encoder/greenpath_encoder/encode.py",
                  "src/model-training/greenpath_training/train.py", "src/model-deploy/greenpath_model/region.py", "docker-compose.ml.yml", "src/ml-workflow/run_pipeline.py"]
    summary["code_sha256"] = {name: digest(ROOT / name) for name in code_paths}
    try:
        if not args.no_build:
            execute(command + ["build"], env, logs / "build.log")
        execute(command + ["up", "--abort-on-container-failure"], env, logs / "ml.log")
        raw_status = subprocess.check_output(command + ["ps", "-a", "--format", "json"], cwd=ROOT, env=env, text=True)
        statuses = parse_status(raw_status)
        expected_services = {"data-processor", "feature-encoder", "model-training", "model-deploy"}
        if {r["Service"] for r in statuses} != expected_services or any(r.get("State") != "exited" or int(r.get("ExitCode", -1)) != 0 for r in statuses):
            raise ValueError("All four ML jobs must exit successfully")
        write_json(run_dir / "containers.json", statuses)
        summary["ml"] = verify_outputs(run_dir)
        refresh_runs()
        register_bundle(run_dir / "serving", label=args.catalog_label, make_default=True, run_id=run_id)
        if not args.no_app:
            app_env = dict(env, API_PORT=str(args.api_port), FRONTEND_PORT=str(args.frontend_port),
                MODEL_PROVIDER="bundle", MODEL_BUNDLE_DIR="/artifacts/pipeline/runs/" + run_id + "/serving", LLM_PROVIDER="mock")
            app = ["docker", "compose", "-p", "greenpath-pipeline-app", "-f", "docker-compose.yml"]
            options = ["up", "-d", "--wait", "--wait-timeout", "120"]
            if not args.no_build:
                options.append("--build")
            execute(app + options, app_env, logs / "app.log")
            summary["app"] = check_api(args.api_port, run_dir, summary["ml"])
            with urllib.request.urlopen("http://127.0.0.1:%d" % args.frontend_port, timeout=30) as response:
                if response.status != 200:
                    raise ValueError("Frontend did not return HTTP 200")
            summary["app"]["frontend_url"] = "http://localhost:%d" % args.frontend_port
        summary["success"] = True
    except Exception as error:
        summary["error"] = str(error)
        raise
    finally:
        summary["elapsed_seconds"] = round(time.time() - started, 2)
        summary["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_json(run_dir / "run-summary.json", summary)
        print(json.dumps(summary, indent=2), flush=True)
    latest = runs.parent / "latest.json"
    atomic_json(latest, {"run_id": run_id, "run_directory": str(run_dir), "summary": summary})


if __name__ == "__main__":
    main()
