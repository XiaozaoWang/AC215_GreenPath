# GreenPath 数据处理（data-processor）

读 data-labeler 打好的分，做成训练用的数据表。逻辑最早写在两个 notebook 里（检测规则、训练数据准备），现在全部在 `cli.py`，不需要再看或运行那两个 notebook。
不调 Gemini、不下载图片、不训练模型，也不改原始标签。用法见 `cli.py` 开头。

## 运行

```sh
sh docker-shell.sh                                    # 进入容器，自己运行 python cli.py ...
sh docker-shell.sh cli.py --prepare                   # 直接跑完整准备，结果默认写到 gs://<bucket>/training/<dataset-id>_<时间>/
sh docker-build-push.sh                               # 推镜像到 Artifact Registry，给 Vertex 流程用
```

在 Vertex 流程里结果要写进 bucket，并且每次用一个新的 id（已存在的目录会报错，旧训练集不会被覆盖）：

```sh
cli.py --prepare --training-id <新id> --output-dir gs://<bucket>/training/<新id>
```

## `--prepare` 做了什么

1. 读入格子、地图特征、街景、航拍和街景的分数，检查数据集完整、各表对得上；航拍图不能用的格子（`tile_ok` 为假，labeler 不打分）直接去掉。
2. **先分组再删格子**：4 个地点整个留作 Test（3 km 内的相邻区域一起），其余按 H3 第 8 级大格子的哈希分 Train/Val 和 5 折（seed 42）。
   数据集里一个 Test 地点都没有（例如试跑的单区域数据集）就不分 Test，只有 Train / Val。
3. 删掉可疑格子（整个格子删掉，不改分）：
   - 第 1 轮：下面三条检测规则现场跑一遍；
   - 第 2 轮：人工审核标记，一份冻结的 CSV `{数据集}/corrections/v2/manual_flags.csv`（目前只有 1026 有；
     找不到就跳过，新城市照常运行；`--manual-flags-csv none` 可主动关掉）。
4. 街景分校准到航拍尺度：分位数映射，**只用 Train + Val 拟合**，Test 不参与。
5. 按固定权重融合街景和航拍；`visual_clutter` 只用街景，`hilly_terrain` 不用。目标是 0–1，缺的分保持空值。
6. 拼成 87 列的训练表，写出 `dataset.parquet`、`exclusions.parquet`、`meta.json`。

对 1026，现场检测找出的格子和当时存下的旧清单（`corrections/v1/candidates.parquet` + 看错 + 隧道）完全相同，
所以不再读那份旧清单，结果仍和 `full_v2` 一样。

## 只检测（不带 `--prepare`）

输出 `candidates.parquet`，只列出可疑的图片 / 格子，不删任何东西：

- 规则 1：街景照片在室内 / 隧道 / 桥底拍的（看 Gemini 写的说明）；
- 规则 2：航拍图所有维度都是最低分，或"至少 14 个 1 分但 open_landscape 是 5 分"；
- 规则 3：郊区农田 / 草地分很低，但周围邻居一致很高。

## 测试和结果核对

```sh
uv run --locked python -m unittest discover -s tests
```

在本地冻结快照 `outputs/dev/f1-preparation-parity/snapshot/` 上，容器结果和原来 notebook 的结果完全一致（时间戳除外），
也和存好的 `training/full_v2/dataset.parquet` 一致：**25,380 个格子 / 87 列**，Train 18,398 · Val 2,876 · Test 4,106，删掉 516 个格子。
