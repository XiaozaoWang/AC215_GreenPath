"""
把训练好的模型部署成 Cloud Run 上的模型服务（代码在 src/model-service）。结构参考 tutorial dlops-io/model-deployment 的 cli.py。

和 tutorial 的区别：tutorial 部署到 Vertex AI Endpoint（Google 替我们运行一个现成的服务），我们部署到 Cloud Run（服务是自己写的 model-service），
所以不用 Model Registry：模型版本已经记在 bucket 的 results/<training-id>/final/<run_id>/ 和它的 config.json 里。
    tutorial                                            我们
    --prepare  下载模型、加上预处理、存到 bucket             --prepare        从 bucket 取训练好的模型，连同预处理代码 network.py 放进 model-service/artifacts/
    --test     部署前在本地测试                          --test --local   测本地运行的 model-service
    --deploy   上传到 Model Registry、部署到 Endpoint      --deploy         把 model-service 镜像部署成 Cloud Run 服务（私有：只有授权的人和服务能调用）
    --predict  调 Endpoint 预测                          --test           测 Cloud Run 上的服务
    --cleanup  撤下 Endpoint（不撤会一直收费）              不需要：Cloud Run 没有请求时缩到 0 个实例，不收费

完整流程（括号里是在哪运行）：
    1. python cli.py --prepare            （这个容器）准备模型
    2. sh docker-shell.sh                 （Mac 的另一个终端，在 model-service/ 下）本地启动服务
    3. python cli.py --test --local       （这个容器）本地服务的分数对不对
    4. sh docker-build-push.sh            （Mac，在 model-service/ 下）推镜像
    5. python cli.py --deploy             （这个容器）部署到 Cloud Run
    6. python cli.py --test               （这个容器）云上服务的分数对不对

--test 就是 L17 讲的 feature parity check：同样的格子，服务现在算出的分数，要和训练时（task.py 第 5 步）存下的预测一致
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
import pandas as pd
import requests
from google.api_core.exceptions import NotFound
from google.cloud import run_v2

# 项目、区域、bucket 都来自 .env（docker-shell.sh 用 --env-file 传进来）
GCP_PROJECT = os.environ["GOOGLE_CLOUD_PROJECT"]
GCP_REGION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
BUCKET = os.environ["GREENPATH_BUCKET"]

# 部署哪个模型：gs://<bucket>/results/<TRAINING_ID>/final/<RUN_ID>/（只部署正式训练的结果，不部署 dry run）
TRAINING_ID = "full_v2"
RUN_ID = "dinov2_s-linear-seed42-ebcc811f"        # 写 "latest" = 用 latest.json 记的最新一次正式训练

# 能打分的区域：这些数据集（collector 采集的）里下载好航拍图的格子。加城市 = 采集完把数据集名加进来，再 --deploy
DATASETS = ["1026_Full_data"]

# Cloud Run 服务
SERVICE_NAME = "greenpath-model-service"
IMAGE = f"us-central1-docker.pkg.dev/{GCP_PROJECT}/greenpath/{SERVICE_NAME}:latest"    # model-service/docker-build-push.sh 推的镜像
SERVICE_ACCOUNT = f"{SERVICE_NAME}@{GCP_PROJECT}.iam.gserviceaccount.com"             # 服务运行时的身份（工牌），只需要读 bucket
CPU, MEMORY = "4", "4Gi"                          # 每个实例的大小
MAX_INSTANCES = 30                                # 最多同时开几个实例：既是并行度上限，也是费用上限
LOCAL_URL = "http://host.docker.internal:8080"    # 本地的 model-service（docker-shell.sh 启动的），从这个容器里看到的地址

ARTIFACTS = "../model-service/artifacts"                       # --prepare 把模型放在这里，build 时打进镜像
NETWORK_PY = "../model-training/package/trainer/network.py"    # 训练用的模型代码（搭模型、预处理、装权重）
N_TEST = 50          # --test 抽几个格子
TOLERANCE = 0.01     # 允许的最大差别：训练时在 GPU 上用半精度（float16）算，服务在 CPU 上用全精度，数字会差一点点


# --prepare：把要部署的模型放进 model-service/artifacts/（相当于 tutorial 的 prepare）
def prepare(fs):
    run_id = RUN_ID
    if run_id == "latest":
        run_id = json.loads(fs.cat(f"{BUCKET}/results/{TRAINING_ID}/final/latest.json"))["run_id"]
    src = f"{BUCKET}/results/{TRAINING_ID}/final/{run_id}"

    # 清空 artifacts/，放进 3 个文件：权重和模型信息从 bucket 下载，network.py 从训练代码复制（保证预处理和训练一模一样）
    shutil.rmtree(ARTIFACTS, ignore_errors=True)
    os.makedirs(ARTIFACTS)
    for name in ["best_model.pt", "config.json"]:
        fs.get(f"{src}/{name}", f"{ARTIFACTS}/{name}")
    shutil.copy(NETWORK_PY, f"{ARTIFACTS}/network.py")

    # 打印这个模型的成绩，确认选对了
    cfg = json.load(open(f"{ARTIFACTS}/config.json"))
    print(f"model: gs://{src}/")
    print(f"val MAE {cfg['val_mae']:.4f} · acc ±0.10 {cfg['val_acc10']:.3f} · {len(cfg['targets'])} scores · best epoch {cfg['best_epoch']}")
    print("next: in model-service/, sh docker-shell.sh (try it locally) or sh docker-build-push.sh (push it for --deploy)")


# --deploy：把 model-service 镜像部署成 Cloud Run 服务（相当于 tutorial 的 deploy）。服务不存在就新建，存在就更新
# 用 Cloud Run 的 Python API（tutorial 用的是 Vertex 的 Python API），效果等于 gcloud run deploy ... --no-allow-unauthenticated
def deploy():
    client = run_v2.ServicesClient()
    name = f"projects/{GCP_PROJECT}/locations/{GCP_REGION}/services/{SERVICE_NAME}"

    # 服务的设置
    service = run_v2.Service(template=run_v2.RevisionTemplate(
        # 版本（revision）名带上时间：每次部署都建一个新版本、重新拉 :latest 镜像；新版本有问题，能在控制台把流量切回旧版本
        revision=f"{SERVICE_NAME}-{time.strftime('%Y%m%d-%H%M%S')}",
        service_account=SERVICE_ACCOUNT,
        # 实例数：没有请求时 0 个（不收费，代价是下一个请求要等冷启动）；最多 MAX_INSTANCES 个
        scaling=run_v2.RevisionScaling(min_instance_count=0, max_instance_count=MAX_INSTANCES),
        # 每个实例一次只处理一个请求：打分会占满 CPU，同时来的请求交给别的实例，并行算更快
        max_instance_request_concurrency=1,
        containers=[run_v2.Container(
            image=IMAGE,
            resources=run_v2.ResourceRequirements(limits={"cpu": CPU, "memory": MEMORY}),
            # 传给服务的环境变量（scorer.py 读）：读哪个 bucket、哪些区域能打分、用几个 CPU 线程
            env=[run_v2.EnvVar(name=k, value=v) for k, v in {
                "GREENPATH_BUCKET": BUCKET, "DATASETS": ",".join(DATASETS), "TORCH_THREADS": CPU}.items()],
        )],
    ))

    # 已经有这个服务就更新（建一个新版本），没有就新建。没有给 allUsers 调用权限，所以服务是私有的
    try:
        client.get_service(name=name)
        service.name = name
        operation = client.update_service(service=service)
        print(f"updating {SERVICE_NAME} ...")
    except NotFound:
        operation = client.create_service(parent=f"projects/{GCP_PROJECT}/locations/{GCP_REGION}",
                                          service=service, service_id=SERVICE_NAME)
        print(f"creating {SERVICE_NAME} ...")
    result = operation.result()                   # 等部署完（新服务要启动、加载模型，约 1–3 分钟）
    print("deployed:", result.uri)
    print("next: python cli.py --test")


# 拿一张身份令牌（ID token）：调私有的 Cloud Run 服务时放在请求头里，证明"我是谁"。这里用的是你的 gcloud 登录
def id_token():
    creds, _ = google.auth.default()
    creds.refresh(google.auth.transport.requests.Request())
    return creds.id_token


# --test：检查模型服务算出的分数对不对（feature parity check），相当于 tutorial 的 test 和 predict
# 抽 N_TEST 个验证集格子，调服务的 /predict，和训练时存下的 predictions.parquet 逐格对比
def test(fs, local):
    # 调哪个服务：本地的，或 Cloud Run 上的（地址问 Cloud Run 要；私有服务要带身份令牌）
    if local:
        url, headers = LOCAL_URL, {}
    else:
        url = run_v2.ServicesClient().get_service(name=f"projects/{GCP_PROJECT}/locations/{GCP_REGION}/services/{SERVICE_NAME}").uri
        headers = {"Authorization": f"Bearer {id_token()}"}
    print("service:", url)

    # 第 1 步：服务活着吗？部署的是哪个模型？（冷启动要先加载模型，第一次可能要等几十秒）
    health = requests.get(f"{url}/health", headers=headers, timeout=300)
    health.raise_for_status()
    h = health.json()
    print(f"model {h['run_id']} (training set {h['training_id']}) · {h['cells']:,} cells can be scored")

    # 第 2 步：取这个模型训练时对验证集的预测，抽 N_TEST 个格子（random_state=0：每次抽同一批）
    targets = h["targets"]
    expected = pd.read_parquet(io.BytesIO(fs.cat(f"{BUCKET}/results/{h['training_id']}/final/{h['run_id']}/predictions.parquet")))
    expected = expected.sample(N_TEST, random_state=0)
    h3 = expected.cell_id.str.rsplit("_", n=1).str[1]           # cell_id = <区域名>_<H3 编号>

    # 第 3 步：同样的格子，让服务现在再打一次分（没打分的格子 scores 是 null，这一行就是空的，算不一致）
    r = requests.post(f"{url}/predict", json={"h3_indices": h3.tolist()}, headers=headers, timeout=300)
    r.raise_for_status()
    got = pd.DataFrame([x["scores"] or {} for x in r.json()["results"]], index=expected.index, columns=targets)

    # 第 4 步：逐格对比：每个格子看 15 个分数里差得最多的那个，超过 TOLERANCE 就算不一致
    diff = (got - expected[targets]).abs().max(axis=1)
    bad = diff.isna() | (diff > TOLERANCE)
    print(f"{(~bad).sum()}/{len(diff)} cells match the training-time predictions "
          f"(largest difference {diff.max():.4f}, tolerance {TOLERANCE})")
    if bad.any():
        print("FAIL, mismatched cells:", expected.cell_id[bad].tolist()[:10])
        sys.exit(1)
    print("PASS")


def main():
    p = argparse.ArgumentParser(description="GreenPath model deployment")
    p.add_argument("--prepare", action="store_true", help="put the trained model into model-service/artifacts/")
    p.add_argument("--deploy", action="store_true", help="deploy the model-service image to Cloud Run")
    p.add_argument("--test", action="store_true", help="compare the service's scores with the training-time predictions")
    p.add_argument("--local", action="store_true", help="with --test: test the local model-service (docker-shell.sh) instead of Cloud Run")
    a = p.parse_args()

    fs = gcsfs.GCSFileSystem(token="google_default")    # 读 bucket，用挂进来的 gcloud 登录
    if a.prepare:
        prepare(fs)
    if a.deploy:
        deploy()
    if a.test:
        test(fs, a.local)


if __name__ == "__main__":
    main()
