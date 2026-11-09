"""
流程里和模型有关的步骤（和 tutorial 的 workflow/model.py 一样）：现在只有 model_training；以后部署步骤 model_deploy 也放这里。
cli.py 用 from model import model_training 导入。

和前三步不一样：前三步是 container component（用我们自己推的镜像）；这一步是 Python component——
Vertex 起一个 python:3.12 的小容器运行下面这个函数，函数再去 Vertex AI 开一台训练机器（CustomJob），等它跑完。
训练代码是 model-training/package-trainer.sh 上传到 gs://<bucket>/trainer/ 的代码包，不用推镜像。
"""
from kfp import dsl


# 训练步骤：提交一个 CustomJob（Vertex 开一台训练机器运行 trainer.task），然后等它结束；训练失败这一步就失败，流程停下
# 不用 tutorial 的 CustomPythonPackageTrainingJob：它要一个和任务同区域的输出目录，我们的 bucket 是多区域 US，会被拒
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

    # Python component 的函数在另一个容器里单独运行，用到的库要在函数里面 import
    import time
    from google.cloud import aiplatform_v1 as v1

    # 训练机器的镜像：CPU 试跑用 scikit-learn 镜像（torch 开机时现装）；正式训练用 PyTorch GPU 镜像 + 一张 T4
    cpu_image = "us-docker.pkg.dev/vertex-ai/training/sklearn-cpu.1-6:latest"
    gpu_image = "us-docker.pkg.dev/vertex-ai/training/pytorch-gpu.2-4.py310:latest"
    machine = v1.MachineSpec(machine_type="n1-standard-4")
    if not dry_run:
        machine = v1.MachineSpec(machine_type="n1-standard-4", accelerator_type=v1.AcceleratorType.NVIDIA_TESLA_T4, accelerator_count=1)

    # 交给 trainer.task 的参数：读 training/<training_id>/，写 results/<training_id>/{dry|final}/
    args = [f"--bucket={bucket_name}", f"--training-id={training_id}", "--dry-run" if dry_run else "--no-dry-run"]

    # 一个训练任务 = 一台机器 + 镜像 + 代码包 + 开机后运行 python -m trainer.task + 参数
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

    # 等训练结束：每 30 秒查一次状态，状态变了就打印（在流程页面这一步的日志里能看到）
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
