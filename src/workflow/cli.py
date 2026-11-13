"""
GreenPath workflow on Vertex AI Pipelines: chains collect, label, process and train into one pipeline.
Only defines the steps (STEPS) and which ones each flag runs (PIPELINES), compiles to yaml and submits to Vertex. No data work here.
Run in the container from docker-shell.sh:
  python cli.py --pipeline          collect, label, process, train
  python cli.py --data_pipeline     collect, label, process (no training, check the training table first)
  python cli.py --data_collector | --data_labeler | --data_processor | --model_training    just that one step
For --model_training, run package-trainer.sh in model-training first to upload the trainer code.
"""
import os
import argparse
import random
import string
from kfp import dsl
from kfp import compiler
import google.cloud.aiplatform as aip
from model import model_training as model_training_job   # the training step is defined in model.py (same as the tutorial)

# Project, region and bucket all come from .env (docker-shell.sh passes them in with --env-file)
GCP_PROJECT = os.environ["GOOGLE_CLOUD_PROJECT"]
GCP_REGION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
GCS_BUCKET_NAME = os.environ["GREENPATH_BUCKET"]
BUCKET_URI = f"gs://{GCS_BUCKET_NAME}"
PIPELINE_ROOT = f"{BUCKET_URI}/pipeline_root/root"       # where Vertex keeps the intermediate records of each run

# The pipeline runs in the cloud as this service account (created in step 5); you submit it with your own gcloud login
# Every step has to get GOOGLE_CLOUD_PROJECT via set_env_variable: Vertex runs the containers in a Google-internal "tenant project",
# so the project the container finds on its own is that internal one, and without it we'd look for Secret Manager and call Gemini in the wrong project
GCS_SERVICE_ACCOUNT = f"greenpath-ml-workflow@{GCP_PROJECT}.iam.gserviceaccount.com"

# Three images: pushed to Artifact Registry in step 3 with each one's docker-build-push.sh
DATA_COLLECTOR_IMAGE = f"us-central1-docker.pkg.dev/{GCP_PROJECT}/greenpath/greenpath-data-collector:latest"
DATA_LABELER_IMAGE = f"us-central1-docker.pkg.dev/{GCP_PROJECT}/greenpath/greenpath-data-labeler:latest"
DATA_PROCESSOR_IMAGE = f"us-central1-docker.pkg.dev/{GCP_PROJECT}/greenpath/greenpath-data-processor:latest"

CONFIG = "configs/test_collector.yaml"         # config file in data-collector, which AREAS we expect to run this time
AREAS = ["city_wa_laconner_edge"]              # for the real run switch to the real config file and dataset, AREAS = ["all"]. if AREAS doesn't cover the areas in the CONFIG yaml, data_processing will fail with n/29
DATASET = "Yue_20261108_test_collector"        #collector creates it, labeler writes labels/, processor reads it, collection also gets it via --dataset, rename only here
TRAINING_ID = "Yue_20261108_test_collector"    #processor writes dataset.parquet, exclusions.parquet, meta.json, an existing folder under training/ is never overwritten, to rerun processing use a new name

# Training: True = dry run (CPU, 96+48 cells, 2 epochs, just proves it runs); False = real training (needs GPU and T4 quota)
# Results go to gs://<bucket>/results/<TRAINING_ID>/{dry|final}/<run_id>/
DRY_RUN = True


def generate_uuid(length: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


# ---------- Layer 1: each step is defined only once ----------

# Collect step (container component): uses the data-collector image
# command=[] means use the image's own ENTRYPOINT (docker-entrypoint.sh) and pass args to it, so the container actually runs:
#   uv run python cli.py --config ... --dataset ... --areas ... --bucket ...
@dsl.container_component
def data_collector():
    return dsl.ContainerSpec(
        image=DATA_COLLECTOR_IMAGE,
        command=[],
        args=["cli.py", "--config", CONFIG, "--dataset", DATASET, "--areas", *AREAS, "--bucket", GCS_BUCKET_NAME],
    )


# Label step: uses the data-labeler image, scores the images collected in datasets/<DATASET>/
@dsl.container_component
def data_labeler():
    return dsl.ContainerSpec(
        image=DATA_LABELER_IMAGE,
        command=[],
        args=["cli.py", "--dataset", DATASET, "--areas", *AREAS, "--bucket", GCS_BUCKET_NAME],
    )


# Process step: uses the data-processor image, reads the scores in datasets/<DATASET>/, writes to training/<TRAINING_ID>/
@dsl.container_component
def data_processor():
    return dsl.ContainerSpec(
        image=DATA_PROCESSOR_IMAGE,
        command=[],
        args=["cli.py", "--prepare", "--bucket", GCS_BUCKET_NAME, "--dataset-id", DATASET,
              "--training-id", TRAINING_ID, "--output-dir", f"{BUCKET_URI}/training/{TRAINING_ID}"],
    )


# The training step is defined in model.py (Python component), here we only fill in this run's values


# Step table: name, and how to build that step (display name, machine size, args)
# Wrapped in lambda: steps can only be built inside @dsl.pipeline, so they're only called when run() needs them
STEPS = {
    # Collect: reads the OSM map of a whole state, so give it a bigger machine
    "collector": lambda: data_collector().set_display_name("Data Collector").set_cpu_limit("4").set_memory_limit("16G"),
    "labeler": lambda: data_labeler().set_display_name("Data Labeler"),
    "processor": lambda: data_processor().set_display_name("Data Processor"),
    "training": lambda: model_training_job(
        project=GCP_PROJECT,
        location=GCP_REGION,
        bucket_name=GCS_BUCKET_NAME,
        training_id=TRAINING_ID,
        dry_run=DRY_RUN,
        service_account=GCS_SERVICE_ACCOUNT,
    ).set_display_name("Model Training"),
}


# ---------- Layer 2: chain the steps from a list into a pipeline, compile, submit ----------

def run(name, steps):
    print(f"{name}: {' → '.join(steps)}")

    # Define the pipeline: build each step in order, each step waits for the previous one to finish (.after)
    @dsl.pipeline(name=name.replace("_", "-"))
    def ml_pipeline():
        prev = None
        for step in steps:
            task = STEPS[step]().set_env_variable("GOOGLE_CLOUD_PROJECT", GCP_PROJECT)
            if prev is not None:
                task.after(prev)
            prev = task

    # Compile to yaml (the pipeline spec Vertex understands), e.g. pipeline.yaml, data_collector.yaml
    compiler.Compiler().compile(ml_pipeline, package_path=f"{name}.yaml")

    # Submit to Vertex AI, runs as the service account
    aip.init(project=GCP_PROJECT, location=GCP_REGION, staging_bucket=BUCKET_URI)

    job_id = generate_uuid()
    DISPLAY_NAME = f"greenpath-{name.replace('_', '-')}-{job_id}"
    job = aip.PipelineJob(
        display_name=DISPLAY_NAME,
        template_path=f"{name}.yaml",
        pipeline_root=PIPELINE_ROOT, #intermediate records of each run go to gs://<bucket>/pipeline_root/root
        enable_caching=False,        #with True (cache on), if Vertex sees a step has the same inputs as last time it skips it and reuses the last result. now False turns it off so every run really reruns
    )

    job.run(service_account=GCS_SERVICE_ACCOUNT)


# ---------- Layer 3: each flag = a list of "which steps to run" ----------
# To add a new combo just add a line here, the command line flag --<name> shows up automatically
PIPELINES = {
    "pipeline": ["collector", "labeler", "processor", "training"],
    "data_pipeline": ["collector", "labeler", "processor"],
    "data_collector": ["collector"],
    "data_labeler": ["labeler"],
    "data_processor": ["processor"],
    "model_training": ["training"],
}


def main(args=None):
    print("CLI Arguments:", args)

    # Run the list for whichever flag is on
    for name, steps in PIPELINES.items():
        if getattr(args, name):
            run(name, steps)


if __name__ == "__main__":
    # python cli.py --help shows these flags (generated from PIPELINES)
    parser = argparse.ArgumentParser(description="GreenPath Workflow CLI")

    for name, steps in PIPELINES.items():
        parser.add_argument(f"--{name}", action="store_true", help=" → ".join(steps))

    args = parser.parse_args()

    main(args)
