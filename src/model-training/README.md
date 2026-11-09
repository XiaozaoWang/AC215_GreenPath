# GreenPath 模型训练（model-training）

读 data-processor 做好的训练表，在 Vertex AI 上训练模型：输入一张 256×256 航拍图，输出 15 个 0–1 的分数。
这里只放训练代码；训练由流程一键运行（`workflow/cli.py --pipeline` 或 `--model_training`），不单独提交。

```text
model-training/
├── package/
│   ├── setup.py            训练机器开机时按它装依赖
│   └── trainer/
│       ├── task.py         训练（在 Vertex 的训练机器上运行）：读数据 → 训练 → 存结果
│       └── network.py      模型的设计图：搭模型、处理图片、存取权重（训练和部署共用）
└── package-trainer.sh      打包 package/，上传到 gs://<bucket>/trainer/greenpath-trainer.tar.gz
```

不需要 Dockerfile：训练机器用 Google 现成的训练镜像（CPU：`sklearn-cpu`，GPU：`pytorch-gpu`），开机后装上代码包，运行 `python -m trainer.task`。

## 运行

```sh
sh package-trainer.sh            # 1. 改了训练代码后上传一次（在 Mac 上运行，要先装好、登录好 gcloud）
```

2. 在 workflow 容器里运行 `python cli.py --pipeline`（整条流程）或 `python cli.py --model_training`（只训练）。
   训练哪个训练集、试跑还是正式训练：改 `workflow/cli.py` 顶部的 `TRAINING_ID` 和 `DRY_RUN`。
   dry run（默认）= CPU、96+48 个格子、2 轮，几分钟；正式训练 = 全部 train + val、最多 25 轮，要 T4 配额。

## 输入和输出

| | 位置 |
|---|---|
| 读 | `gs://<bucket>/training/<training-id>/dataset.parquet`、`meta.json`（data-processor 写的）+ 表里 `tile_path` 指向的航拍图 |
| 写 | `gs://<bucket>/results/<training-id>/{dry,final}/<run_id>/`：`best_model.pt`（部署用）、`config.json`、`history` / `metrics` / `predictions.parquet` |
| | `gs://<bucket>/results/<training-id>/{dry,final}/latest.json`：最新的是哪个 run |

Test 行读进来就丢掉，训练不用；没有 Test 的训练集（单区域试跑）也能训练。

## 最终模型

在 Colab T4 上用 `full_v2` 训练（Vertex 目前没有 GPU 配额）：
`gs://<bucket>/results/full_v2/final/dinov2_s-linear-seed42-ebcc811f/best_model.pt`，验证 MAE 0.0550，测试 MAE 0.0699。
来历和实验结果见 `1107-vertexAI/HANDOFF_*.md`。
