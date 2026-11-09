# GreenPath 数据版本（data-versioning）

用 DVC 给训练集拍快照：以后任何时候都能把"某次训练用的数据"原样拿回来。结构照 tutorial [dlops-io/data-versioning](https://github.com/dlops-io/data-versioning)。
不在 Vertex 流程里：流程跑完、做好新的训练集后，手动来这里存一版。

```text
data-versioning/
├── Dockerfile               装 git + DVC 的镜像
├── docker-shell.sh          build + 进容器（挂整个仓库 + gcloud 登录凭证 + .env）
├── docker-entrypoint.sh     启用环境，留一个终端
├── pyproject.toml / uv.lock  Python 包：dvc、dvc-gs、gcsfs、pandas
├── cli.py                   把一个训练集下载到 greenpath_dataset/（相当于 tutorial 的 gcsfuse 挂载）
├── greenpath_dataset.dvc    "纸条"：这一版数据的 hash，进 git（dvc add 生成）
└── greenpath_dataset/       真实数据，不进 git（cli.py 下载）
```

## 存什么

| 内容 | 来自 bucket 的 | 大小（test / full_v2） |
|---|---|---|
| 训练表 `dataset.parquet`、`meta.json`、`exclusions.parquet` | `training/<training-id>/` | 约 0.1 MB |
| 表里 `tile_path` 指向的航拍图（train / val / test 全部） | `cache/naip_tiles/...` | 282 张约 30 MB / 25,380 张约 2.5 GB |

不存：`datasets/`（中间产物）、街景图和 OSM（训练不用）、`embeddings_*.parquet`（训练不用）、模型（在 `results/`）。

三个地方各管一样：

| | 放什么 |
|---|---|
| git | `greenpath_dataset.dvc` 纸条 + `.dvc/config`；git tag 给每一版起名字 |
| `gs://<bucket>/dvc_store/` | 每一版的清单 + 所有文件（按 hash 存，不同版本里相同的图只存一份） |
| Mac 上的 `.dvc/cache/` | 本地的同一份（不进 git） |

## 第一次：初始化（只做一次）

```sh
sh docker-shell.sh                                               # Mac 上，在 src/data-versioning/ 下
dvc init --subdir                                                # 容器里：在这个子文件夹建 .dvc/（仓库根目录不动）
dvc remote add -d greenpath_dataset gs://$GREENPATH_BUCKET/dvc_store   # 容器里：数据推到 bucket 的 dvc_store/
```

## 存一版数据

```sh
python cli.py --training-id Yue_20261108_test_collector     # 容器里：下载训练表 + 用到的图到 greenpath_dataset/
dvc add greenpath_dataset                                     # 容器里：算 hash，写 greenpath_dataset.dvc
dvc push                                                      # 容器里：数据推到 dvc_store/
```

然后在 **Mac 上**（不在容器里）提交纸条、打 tag：

```sh
git add src/data-versioning
git commit -m "Data version: Yue_20261108_test_collector"
git tag -a dataset_Yue_20261108_test_collector -m "training/Yue_20261108_test_collector + 282 tiles"
git push --atomic origin milestone3 dataset_Yue_20261108_test_collector
```

下一版：换 `--training-id` 重复这一节，tag 用新名字。

## 拿回某一版

```sh
git checkout dataset_Yue_20261108_test_collector   # Mac 上：纸条回到那一版
dvc pull                                            # 容器里：按纸条从 dvc_store/ 下载，greenpath_dataset/ 变回那一版
git checkout milestone3                             # Mac 上：用完回到分支
```
