"""
GreenPath 流程（Vertex AI Pipelines）：把 采集 → 打分 → 处理 → 训练 四步串成流程，在 Google 的机器上运行。
本身不处理数据，只负责“定义步骤 → 串成流程 → 编译成 yaml → 提交给 Vertex”。参考 tutorial 的 workflow/cli.py，把重复的步骤定义合并成一份。

写法：每一步只定义一次（STEPS），每个开关是一张"跑哪几步"的清单（PIPELINES），run() 按清单把步骤串起来。
加新组合 = 在 PIPELINES 里加一行；开关会自动出现。

用法（在 docker-shell.sh 打开的容器里）：
  python cli.py --pipeline            采集 → 打分 → 处理 → 训练
  python cli.py --data_pipeline       采集 → 打分 → 处理（不训练，先检查训练表）
  python cli.py --data_collector      只跑采集
  python cli.py --data_labeler        只跑打分
  python cli.py --data_processor      只跑处理（读打好的分，做成训练表）
  python cli.py --model_training      只跑训练（读训练表，训练模型；先在 model-training 里运行 package-trainer.sh 上传代码）
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
DATASET = "Yue_20261108_test_collector"        #collector 建，labeler 写 labels/，processor 读，采集也通过 --dataset 拿到它，改名只改这里
TRAINING_ID = "Yue_20261108_test_collector"    #processor 写 dataset.parquet、exclusions.parquet、meta.json，training/ 下已存在的文件夹不会被覆盖，重跑处理要换名字

# 训练：True = 试跑（CPU、96+48 个格子、2 轮，只证明能跑通）；False = 正式训练（要 GPU 和 T4 配额）
# 结果写到 gs://<bucket>/results/<TRAINING_ID>/{dry|final}/<run_id>/
DRY_RUN = True


def generate_uuid(length: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


# ---------- 第 1 层：每一步只定义一次 ----------

# 采集步骤（container component）：用 data-collector 镜像
# command=[] = 用镜像自己的 ENTRYPOINT（docker-entrypoint.sh），args 交给它，容器里实际运行：
#   uv run python cli.py --config ... --dataset ... --areas ... --bucket ...
@dsl.container_component
def data_collector():
    return dsl.ContainerSpec(
        image=DATA_COLLECTOR_IMAGE,
        command=[],
        args=["cli.py", "--config", CONFIG, "--dataset", DATASET, "--areas", *AREAS, "--bucket", GCS_BUCKET_NAME],
    )


# 打分步骤：用 data-labeler 镜像，给 datasets/<DATASET>/ 里采好的图打分
@dsl.container_component
def data_labeler():
    return dsl.ContainerSpec(
        image=DATA_LABELER_IMAGE,
        command=[],
        args=["cli.py", "--dataset", DATASET, "--areas", *AREAS, "--bucket", GCS_BUCKET_NAME],
    )


# 处理步骤：用 data-processor 镜像，读 datasets/<DATASET>/ 里打好的分，写到 training/<TRAINING_ID>/
@dsl.container_component
def data_processor():
    return dsl.ContainerSpec(
        image=DATA_PROCESSOR_IMAGE,
        command=[],
        args=["cli.py", "--prepare", "--bucket", GCS_BUCKET_NAME, "--dataset-id", DATASET,
              "--training-id", TRAINING_ID, "--output-dir", f"{BUCKET_URI}/training/{TRAINING_ID}"],
    )


# 训练步骤定义在 model.py（Python component），这里只填这次的值


# 步骤表：名字 → 怎么建这一步（显示名、机器大小、参数）
# 用 lambda 包起来：步骤只能在 @dsl.pipeline 里面建，run() 用到时才调用
STEPS = {
    # 采集：要读整州 OSM 地图，机器给大一点
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


# ---------- 第 2 层：按清单把步骤串成流程，编译，提交 ----------

def run(name, steps):
    print(f"{name}: {' → '.join(steps)}")

    # 定义流程：按顺序建每一步，后一步等前一步跑完才开始（.after）
    @dsl.pipeline(name=name.replace("_", "-"))
    def ml_pipeline():
        prev = None
        for step in steps:
            task = STEPS[step]().set_env_variable("GOOGLE_CLOUD_PROJECT", GCP_PROJECT)
            if prev is not None:
                task.after(prev)
            prev = task

    # 编译成 yaml（Vertex 看得懂的流程说明书），例如 pipeline.yaml、data_collector.yaml
    compiler.Compiler().compile(ml_pipeline, package_path=f"{name}.yaml")

    # 提交到 Vertex AI，以服务账号的身份运行
    aip.init(project=GCP_PROJECT, location=GCP_REGION, staging_bucket=BUCKET_URI)

    job_id = generate_uuid()
    DISPLAY_NAME = f"greenpath-{name.replace('_', '-')}-{job_id}"
    job = aip.PipelineJob(
        display_name=DISPLAY_NAME,
        template_path=f"{name}.yaml",
        pipeline_root=PIPELINE_ROOT, #每次运行的中间记录放gs://<bucket>/pipeline_root/root
        enable_caching=False,        #True打开缓存的话，Vertex 发现某一步的输入和上次一样就直接跳过、沿用上次的结果。目前False关掉以后每次都真的重跑
    )

    job.run(service_account=GCS_SERVICE_ACCOUNT)


# ---------- 第 3 层：每个开关 = 一张"跑哪几步"的清单 ----------
# 加新组合只要在这里加一行，命令行开关 --<名字> 会自动出现
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

    # 打开了哪个开关，就跑对应的清单
    for name, steps in PIPELINES.items():
        if getattr(args, name):
            run(name, steps)


if __name__ == "__main__":
    # 运行 python cli.py --help 可以看到下面这些开关（按 PIPELINES 自动生成）
    parser = argparse.ArgumentParser(description="GreenPath Workflow CLI")

    for name, steps in PIPELINES.items():
        parser.add_argument(f"--{name}", action="store_true", help=" → ".join(steps))

    args = parser.parse_args()

    main(args)
