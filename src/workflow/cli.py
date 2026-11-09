"""
GreenPath 流程（Vertex AI Pipelines）：把 采集 → 打分 → 处理 → 训练 四步串成流程，在 Google 的机器上运行。
本身不处理数据，只负责“定义步骤 → 串成流程 → 编译成 yaml → 提交给 Vertex”。结构和 tutorial 的 workflow/cli.py 一样。

用法（在 docker-shell.sh 打开的容器里）：
  python cli.py --data_collector      只跑采集
  python cli.py --data_labeler        只跑打分
  python cli.py --data_processor      只跑处理（读打好的分，做成训练表）
  python cli.py --model_training      只跑训练（读训练表，训练模型；先在 model-training 里运行 package-trainer.sh 上传代码）
  python cli.py --pipeline            采集 → 打分 → 处理 → 训练，一起跑
"""
import os
import argparse
import random
import string
from kfp import dsl
from kfp import compiler
import google.cloud.aiplatform as aip
from model import model_training as model_training_job   # 训练步骤定义在 model.py（和 tutorial 一样）

# 项目、区域、bucket 都来自 .env（docker-shell.sh 用 --env-file 传进来）
GCP_PROJECT = os.environ["GOOGLE_CLOUD_PROJECT"]
GCP_REGION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
GCS_BUCKET_NAME = os.environ["GREENPATH_BUCKET"]
BUCKET_URI = f"gs://{GCS_BUCKET_NAME}"
PIPELINE_ROOT = f"{BUCKET_URI}/pipeline_root/root"       # Vertex 存放每次运行的中间记录

# 流程在云上以这个服务账号的身份运行（第 5 步建的）；提交流程的是你自己的 gcloud 登录
# 每个步骤都要用 set_env_variable 传入 GOOGLE_CLOUD_PROJECT：Vertex 在 Google 内部的“租户项目”里运行容器，
# 容器自己查到的项目是那个内部项目，不传的话会去错的项目找 Secret Manager、调 Gemini
GCS_SERVICE_ACCOUNT = f"greenpath-ml-workflow@{GCP_PROJECT}.iam.gserviceaccount.com"

# 三个镜像：第 3 步用各自的 docker-build-push.sh 推到 Artifact Registry 的
DATA_COLLECTOR_IMAGE = f"us-central1-docker.pkg.dev/{GCP_PROJECT}/greenpath/greenpath-data-collector:latest"
DATA_LABELER_IMAGE = f"us-central1-docker.pkg.dev/{GCP_PROJECT}/greenpath/greenpath-data-labeler:latest"
DATA_PROCESSOR_IMAGE = f"us-central1-docker.pkg.dev/{GCP_PROJECT}/greenpath/greenpath-data-processor:latest"

CONFIG = "configs/test_collector.yaml"         # data-collector里的配置文件,这次预期一共会跑什么AREAS
AREAS = ["city_wa_laconner_edge"]              # 正式跑时改成正式的配置文件和数据集、AREAS 写 ["all"]，如果AREAS并没有覆盖CONFIG yaml里面的areas，在data_processing部分会报错n/29
DATASET = "Yue_20261108_test_collector"        #collector 建，labeler 写 labels/，processor 读，采集通过 --dataset 拿到它，改名只改这里
TRAINING_ID = "Yue_20261108_test_collector"    #processor 写 dataset.parquet、exclusions.parquet、meta.json，training/ 下已存在的文件夹不会被覆盖，重跑处理要换名字

# 训练：True = 试跑（CPU、96+48 个格子、2 轮，只证明能跑通）；False = 正式训练（要 GPU 和 T4 配额）
# 结果写到 gs://<bucket>/results/<TRAINING_ID>/{dry|final}/<run_id>/
DRY_RUN = True


def generate_uuid(length: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def data_collector():
    print("data_collector()")

    # 定义一个步骤（container component）：用 data-collector 镜像
    # command=[] = 用镜像自己的 ENTRYPOINT（docker-entrypoint.sh），args 交给它，容器里实际运行：
    #   uv run python cli.py --config ... --areas ... --bucket ...
    @dsl.container_component
    def data_collector():
        container_spec = dsl.ContainerSpec(
            image=DATA_COLLECTOR_IMAGE,
            command=[],
            args=["cli.py", "--config", CONFIG, "--dataset", DATASET, "--areas", *AREAS, "--bucket", GCS_BUCKET_NAME],
        )
        return container_spec

    # 定义流程：只有采集这一步
    @dsl.pipeline
    def data_collector_pipeline():
        data_collector().set_env_variable("GOOGLE_CLOUD_PROJECT", GCP_PROJECT)

    # 编译成 yaml（Vertex 看得懂的流程说明书）
    compiler.Compiler().compile(data_collector_pipeline, package_path="data_collector.yaml")

    # 提交到 Vertex AI，以服务账号的身份运行
    aip.init(project=GCP_PROJECT, location=GCP_REGION, staging_bucket=BUCKET_URI)

    job_id = generate_uuid()
    DISPLAY_NAME = "greenpath-data-collector-" + job_id
    job = aip.PipelineJob(
        display_name=DISPLAY_NAME,
        template_path="data_collector.yaml",
        pipeline_root=PIPELINE_ROOT, #每次运行的中间记录放gs://<bucket>/pipeline_root/root
        enable_caching=False,        #True打开缓存的话，Vertex 发现某一步的输入和上次一样就直接跳过、沿用上次的结果。目前False关掉以后每次都真的重跑
    )

    job.run(service_account=GCS_SERVICE_ACCOUNT)


def data_labeler():
    print("data_labeler()")

    # 定义一个步骤：用 data-labeler 镜像
    @dsl.container_component
    def data_labeler():
        container_spec = dsl.ContainerSpec(
            image=DATA_LABELER_IMAGE,
            command=[],
            args=["cli.py", "--dataset", DATASET, "--areas", *AREAS, "--bucket", GCS_BUCKET_NAME],
        )
        return container_spec

    # 定义流程：只有打分这一步
    @dsl.pipeline
    def data_labeler_pipeline():
        data_labeler().set_env_variable("GOOGLE_CLOUD_PROJECT", GCP_PROJECT)

    # 编译成 yaml
    compiler.Compiler().compile(data_labeler_pipeline, package_path="data_labeler.yaml")

    # 提交到 Vertex AI
    aip.init(project=GCP_PROJECT, location=GCP_REGION, staging_bucket=BUCKET_URI)

    job_id = generate_uuid()
    DISPLAY_NAME = "greenpath-data-labeler-" + job_id
    job = aip.PipelineJob(
        display_name=DISPLAY_NAME,
        template_path="data_labeler.yaml",
        pipeline_root=PIPELINE_ROOT,
        enable_caching=False,
    )

    job.run(service_account=GCS_SERVICE_ACCOUNT)


def data_processor():
    print("data_processor()")

    # 定义一个步骤：用 data-processor 镜像，读 datasets/<DATASET>/ 里打好的分，写到 training/<TRAINING_ID>/
    @dsl.container_component
    def data_processor():
        container_spec = dsl.ContainerSpec(
            image=DATA_PROCESSOR_IMAGE,
            command=[],
            args=["cli.py", "--prepare", "--bucket", GCS_BUCKET_NAME, "--dataset-id", DATASET,
                  "--training-id", TRAINING_ID, "--output-dir", f"{BUCKET_URI}/training/{TRAINING_ID}"],
        )
        return container_spec

    # 定义流程：只有处理这一步
    @dsl.pipeline
    def data_processor_pipeline():
        data_processor().set_env_variable("GOOGLE_CLOUD_PROJECT", GCP_PROJECT)

    # 编译成 yaml
    compiler.Compiler().compile(data_processor_pipeline, package_path="data_processor.yaml")

    # 提交到 Vertex AI
    aip.init(project=GCP_PROJECT, location=GCP_REGION, staging_bucket=BUCKET_URI)

    job_id = generate_uuid()
    DISPLAY_NAME = "greenpath-data-processor-" + job_id
    job = aip.PipelineJob(
        display_name=DISPLAY_NAME,
        template_path="data_processor.yaml",
        pipeline_root=PIPELINE_ROOT,
        enable_caching=False,
    )

    job.run(service_account=GCS_SERVICE_ACCOUNT)


def model_training():
    print("model_training()")

    # 定义流程：只有训练这一步（步骤本身定义在 model.py）
    @dsl.pipeline
    def model_training_pipeline():
        model_training_job(
            project=GCP_PROJECT,
            location=GCP_REGION,
            bucket_name=GCS_BUCKET_NAME,
            training_id=TRAINING_ID,
            dry_run=DRY_RUN,
            service_account=GCS_SERVICE_ACCOUNT,
        )

    # 编译成 yaml
    compiler.Compiler().compile(model_training_pipeline, package_path="model_training.yaml")

    # 提交到 Vertex AI
    aip.init(project=GCP_PROJECT, location=GCP_REGION, staging_bucket=BUCKET_URI)

    job_id = generate_uuid()
    DISPLAY_NAME = "greenpath-model-training-" + job_id
    job = aip.PipelineJob(
        display_name=DISPLAY_NAME,
        template_path="model_training.yaml",
        pipeline_root=PIPELINE_ROOT,
        enable_caching=False,
    )

    job.run(service_account=GCS_SERVICE_ACCOUNT)


def pipeline():
    print("pipeline()")

    # 定义采集步骤
    @dsl.container_component
    def data_collector():
        container_spec = dsl.ContainerSpec(
            image=DATA_COLLECTOR_IMAGE,
            command=[],
            args=["cli.py", "--config", CONFIG, "--dataset", DATASET, "--areas", *AREAS, "--bucket", GCS_BUCKET_NAME],
        )
        return container_spec

    # 定义打分步骤
    @dsl.container_component
    def data_labeler():
        container_spec = dsl.ContainerSpec(
            image=DATA_LABELER_IMAGE,
            command=[],
            args=["cli.py", "--dataset", DATASET, "--areas", *AREAS, "--bucket", GCS_BUCKET_NAME],
        )
        return container_spec

    # 定义处理步骤
    @dsl.container_component
    def data_processor():
        container_spec = dsl.ContainerSpec(
            image=DATA_PROCESSOR_IMAGE,
            command=[],
            args=["cli.py", "--prepare", "--bucket", GCS_BUCKET_NAME, "--dataset-id", DATASET,
                  "--training-id", TRAINING_ID, "--output-dir", f"{BUCKET_URI}/training/{TRAINING_ID}"],
        )
        return container_spec

    # 定义流程：采集 → 打分 → 处理 → 训练
    @dsl.pipeline
    def ml_pipeline():
        # 采集：要读整州 OSM 地图，机器给大一点
        data_collector_task = (
            data_collector()
            .set_display_name("Data Collector")
            .set_env_variable("GOOGLE_CLOUD_PROJECT", GCP_PROJECT)
            .set_cpu_limit("4")
            .set_memory_limit("16G")
        )
        # 打分：采集跑完才开始
        data_labeler_task = (
            data_labeler()
            .set_display_name("Data Labeler")
            .set_env_variable("GOOGLE_CLOUD_PROJECT", GCP_PROJECT)
            .after(data_collector_task)
        )
        # 处理：打分跑完才开始
        data_processor_task = (
            data_processor()
            .set_display_name("Data Processor")
            .set_env_variable("GOOGLE_CLOUD_PROJECT", GCP_PROJECT)
            .after(data_labeler_task)
        )
        # 训练：处理跑完才开始（读处理刚写好的 training/<TRAINING_ID>/）
        model_training_task = (
            model_training_job(
                project=GCP_PROJECT,
                location=GCP_REGION,
                bucket_name=GCS_BUCKET_NAME,
                training_id=TRAINING_ID,
                dry_run=DRY_RUN,
                service_account=GCS_SERVICE_ACCOUNT,
            )
            .set_display_name("Model Training")
            .after(data_processor_task)
        )

    # 编译成 yaml
    compiler.Compiler().compile(ml_pipeline, package_path="pipeline.yaml")

    # 提交到 Vertex AI
    aip.init(project=GCP_PROJECT, location=GCP_REGION, staging_bucket=BUCKET_URI)

    job_id = generate_uuid()
    DISPLAY_NAME = "greenpath-pipeline-" + job_id
    job = aip.PipelineJob(
        display_name=DISPLAY_NAME,
        template_path="pipeline.yaml",
        pipeline_root=PIPELINE_ROOT,
        enable_caching=False,
    )

    job.run(service_account=GCS_SERVICE_ACCOUNT)


def main(args=None):
    print("CLI Arguments:", args)

    if args.data_collector:
        data_collector()

    if args.data_labeler:
        data_labeler()

    if args.data_processor:
        data_processor()

    if args.model_training:
        model_training()

    if args.pipeline:
        pipeline()


if __name__ == "__main__":
    # 运行 python cli.py --help 可以看到下面这些开关
    parser = argparse.ArgumentParser(description="GreenPath Workflow CLI")

    parser.add_argument("--data_collector", action="store_true", help="只跑采集")
    parser.add_argument("--data_labeler", action="store_true", help="只跑打分")
    parser.add_argument("--data_processor", action="store_true", help="只跑处理")
    parser.add_argument("--model_training", action="store_true", help="只跑训练")
    parser.add_argument("--pipeline", action="store_true", help="采集 → 打分 → 处理 → 训练")

    args = parser.parse_args()

    main(args)
