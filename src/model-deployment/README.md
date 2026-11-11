# GreenPath 模型部署（model-deployment + model-service）

把训练好的模型（`results/<training-id>/final/<run_id>/best_model.pt`）部署成 Cloud Run 上的一个私有服务：给一批 H3 格子，返回每格 15 个分数。
结构参考 tutorial [dlops-io/model-deployment](https://github.com/dlops-io/model-deployment)。tutorial 部署到 Vertex AI Endpoint，我们部署到 Cloud Run，所以服务（model-service）是自己写的，也不需要 Model Registry。

```text
model-service/               部署到 Cloud Run 的服务（相当于 tutorial 的 Endpoint）
├── main.py                  接口：GET /health、POST /predict（格子 → 分数）、POST /predict_image（上传一张图 → 分数，测试用）
├── scorer.py                启动时加载模型和格子索引；打分
├── artifacts/               模型：best_model.pt、config.json、network.py（--prepare 生成，不进 git，build 时打进镜像）
├── Dockerfile               镜像里有模型和 DINOv2 原厂权重，启动不用下载
├── docker-shell.sh          本地启动服务，http://localhost:8080/docs 试用
└── docker-build-push.sh     推镜像到 Artifact Registry

model-deployment/            在你电脑上运行的部署工具（相当于 tutorial 的 cli.py）
├── cli.py                   --prepare / --deploy / --test
└── Dockerfile、docker-shell.sh、docker-entrypoint.sh
```

## 第一次：GCP 设置（只做一次）

1. **开启 Cloud Run Admin API**：控制台 → APIs & Services → Library → 搜 "Cloud Run Admin API" → Enable
   （或 `gcloud services enable run.googleapis.com`）
2. **建服务账号 `greenpath-model-service`**（服务运行时的身份，只需要读 bucket）：
   控制台 → IAM & Admin → Service accounts → Create service account → 名字填 `greenpath-model-service` → 项目角色不用选 → Done
3. **允许它读 bucket**：控制台 → Cloud Storage → Buckets → 你的 bucket → Permissions → Grant access →
   New principals 填 `greenpath-model-service@<项目>.iam.gserviceaccount.com` → 角色选 Storage Object Viewer → Save
   （或 `gcloud storage buckets add-iam-policy-binding gs://<bucket> --member=serviceAccount:greenpath-model-service@<项目>.iam.gserviceaccount.com --role=roles/storage.objectViewer`）

你自己（项目 Owner）已经有部署和调用 Cloud Run 的权限，不用另外设置。以后 api-service 要调模型服务，再给 api-service 的服务账号加 Cloud Run Invoker。

## 部署一个模型

要部署哪个模型、哪些区域能打分，改 `cli.py` 开头的 `TRAINING_ID`、`RUN_ID`、`DATASETS`。

```sh
# 终端 1（Mac）：进部署工具的容器
cd src/model-deployment && sh docker-shell.sh
python cli.py --prepare              # 容器里：模型放进 model-service/artifacts/

# 终端 2（Mac）：本地启动服务（可以打开 http://localhost:8080/docs 手动试）
cd src/model-service && sh docker-shell.sh

# 终端 1：本地服务的分数和训练时一致吗
python cli.py --test --local

# 终端 2：Ctrl+C 停掉本地服务，推镜像（amd64，Apple 芯片上要几分钟）
sh docker-build-push.sh

# 终端 1：部署到 Cloud Run，再检查一次
python cli.py --deploy
python cli.py --test
```

`--test` 抽 50 个验证集格子，调服务的 `/predict`，和训练时存下的 `predictions.parquet` 逐格对比，差别都在 0.01 以内才算 PASS（训练在 GPU 上用半精度，服务在 CPU 上用全精度，本地实测最大差 0.0024）。

如果 `--test`（云上）报 401/403：在 Mac 上运行 `gcloud run services proxy greenpath-model-service --region us-central1 --port 8080`，它会替你带上身份，然后在容器里运行 `python cli.py --test --local`。

## 调用模型服务

```text
POST /predict   {"h3_indices": ["8a2802374807fff", ...]}      最多 2000 个，更大的区域由 api-service 拆开同时发
→ {"run_id": "...", "results": [{"h3_index": "...", "status": "ok", "scores": {"tree_canopy": 0.99, ...}}, ...]}
```

`status`：`ok` = 打了分；`not_downloaded` = 不在 `DATASETS` 里；`no_naip_coverage` / `mostly_black` = collector 判定航拍图不能用，不打分（`scores` 为 `null`）。

## 费用

没有请求时实例数为 0，不收费；代价是之后的第一个请求要等冷启动（拉镜像加上加载模型）。最多同时开 `MAX_INSTANCES`（30）个实例。
