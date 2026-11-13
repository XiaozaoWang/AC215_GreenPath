"""
Model steps of the pipeline (like workflow/model.py in the tutorial). Only model_training for now; a model_deploy step would go here too.
Unlike the first three steps (container components with our own images), this is a Python component: Vertex starts a small
python:3.12 container that runs the function below, and the function starts a Vertex AI training machine (CustomJob) and waits for it.
The trainer code is the package model-training/package-trainer.sh uploads to gs://<bucket>/trainer/, so no image push.
cli.py imports it with from model import model_training.
"""
from kfp import dsl


# Training step: submit a CustomJob (Vertex starts a training machine that runs trainer.task), then wait for it to end; if training fails this step fails and the pipeline stops
# Not using the tutorial's CustomPythonPackageTrainingJob: it needs an output folder in the same region as the job, our bucket is multi-region US, so it gets rejected
@dsl.component(base_image="python:3.12", packages_to_install=["google-cloud-aiplatform"])
def model_training(
    project: str = "",
    location: str = "",
    bucket_name: str = "",
    training_id: str = "",
    dry_run: bool = True,
    service_account: str = "",
):
    print("Model Training Job")

    # A Python component function runs on its own in another container, so the libraries it uses have to be imported inside the function
    import time
    from google.cloud import aiplatform_v1 as v1

    # Training machine image: the CPU dry run uses the scikit-learn image (torch gets installed at startup); real training uses the PyTorch GPU image + one T4
    cpu_image = "us-docker.pkg.dev/vertex-ai/training/sklearn-cpu.1-6:latest"
    gpu_image = "us-docker.pkg.dev/vertex-ai/training/pytorch-gpu.2-4.py310:latest"
    machine = v1.MachineSpec(machine_type="n1-standard-4")
    if not dry_run:
        machine = v1.MachineSpec(machine_type="n1-standard-4", accelerator_type=v1.AcceleratorType.NVIDIA_TESLA_T4, accelerator_count=1)

    # Args for trainer.task: read training/<training_id>/, write results/<training_id>/{dry|final}/
    args = [f"--bucket={bucket_name}", f"--training-id={training_id}", "--dry-run" if dry_run else "--no-dry-run"]

    # One training job = one machine + image + code package + running python -m trainer.task with the args at startup
    spec = v1.WorkerPoolSpec(
        machine_spec=machine,
        replica_count=1,
        python_package_spec=v1.PythonPackageSpec(
            executor_image_uri=cpu_image if dry_run else gpu_image,
            package_uris=[f"gs://{bucket_name}/trainer/greenpath-trainer.tar.gz"],
            python_module="trainer.task",
            args=args,
        ),
    )
    client = v1.JobServiceClient(client_options={"api_endpoint": f"{location}-aiplatform.googleapis.com"})
    job = client.create_custom_job(
        parent=f"projects/{project}/locations/{location}",
        custom_job=v1.CustomJob(display_name="greenpath-training",
                                job_spec=v1.CustomJobSpec(worker_pool_specs=[spec], service_account=service_account)),
    )
    print("Custom job:", job.name, "args:", args, flush=True)

    # Wait for training to end: check the state every 30 seconds, print it when it changes (shows up in this step's logs on the pipeline page)
    done = (v1.JobState.JOB_STATE_SUCCEEDED, v1.JobState.JOB_STATE_FAILED, v1.JobState.JOB_STATE_CANCELLED, v1.JobState.JOB_STATE_EXPIRED)
    last = None
    while job.state not in done:
        time.sleep(30)
        job = client.get_custom_job(name=job.name)
        if job.state != last:
            print("State:", job.state.name, flush=True)
            last = job.state
    if job.state != v1.JobState.JOB_STATE_SUCCEEDED:
        raise RuntimeError(f"Training job ended as {job.state.name}: {job.error.message}")
