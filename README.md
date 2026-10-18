> **Milestone 2 integration branch — step 1 implemented.** The app now uses a model-owned eight-dimension schema and real Boston H3 baseline test predictions. GCS artifacts are versioned and checksummed in `gs://ac215-greenpath`. Start with [the current integration guide](docs/STEP1.md). This step is an interface/storage foundation, not yet the complete containerized training pipeline. The notebook/data descriptions below remain the research workflow.

# GreenPath · 数据管道（notebook 阶段）

GreenPath 的核心思路是"老师教学生"：老师（VLM，如 Gemini）看**街景**给出"骑过这里感觉如何"的分数（标签 Y），学生（我们的模型）只看**卫星图 + OSM 特征**（输入 X）去学这些分数。街景只在训练时用来生成 Y，推理时不需要。

这个目录里的 notebook 负责把原始数据变成一张"每个格子一行"的训练表。目前完成 01–07，之后的计划见文末。

```
01 街景 ──► 格子主表 cells.parquet ──► 02 卫星图 ──► 03 OSM / WorldCover 特征
                    └──► 04 VLM 标注（Gemini 看街景打分，生成标签 Y）
                                          └──► 05 合并 X、Y，划分 train / val / test，登记版本
                                                   ├──► 06 提取卫星图特征（PyTorch，只用 torch）
                                                   └──► 07 基线与线性探针（LightGBM / sklearn，不用 torch）
```

**流程方向是"街景优先"**：先抓街景，由街景所在位置决定哪些 H3 格子进入数据集，再给这些格子补卫星图和 OSM 特征。这样不会浪费算力给没有街景的格子，也能直接看到真实覆盖率。

---

## 快速开始

1. **环境**：Python 3.11，项目自带的 `.venv`；VS Code 里选 `.venv` 作为 kernel。依赖见各 notebook 第一格（Colab 里会自动安装，本地需提前装好）。
2. **Mapillary token**（只有 01 需要）：复制 `.env.example` 为 `.env`，填入 Mapillary 的 **Access Token**（形如 `MLY|数字|字符串`，不是 App ID，也不是 Client secret）。`.env` 已被 `.gitignore` 忽略，不要提交。
3. **按顺序运行**：01 → 02 → 03，每个都是 **Run All**。后面的 notebook 读前面写出的文件，所以不能乱序。
4. **数据位置**：默认写到 `./data/`。可以用环境变量 `GREENPATH_ROOT` 指定别的目录；每个 notebook 开头的 `STORAGE` 也可以改成 `"gcs"` 或 `"drive"`。

所有 notebook 都**可以断点续跑**：已经下载或生成的文件会自动跳过。

---

## 三个 notebook 分别做什么

| | 做什么 | 读 | 写 |
|---|---|---|---|
| **01** `01_streetview.ipynb` | 用 Mapillary 矢量瓦片批量下载试点区域的街景元数据，过滤，落到 H3 格子，抽样并下载图片 | Mapillary API | `processed/cells.parquet`、`processed/streetview.parquet`、`raw/mapillary/…`、`raw/grid/…` |
| **02** `02_tiles.ipynb` | 给 01 选出的每个格子，从 NAIP 航拍裁一张 256×256 的卫星图 | `cells.parquet` | `processed/tiles/*.png`；给 `cells.parquet` 补卫星图相关的列 |
| **03** `03_osm_features.ipynb` | 统计每个格子对应地面的 OSM 与 WorldCover 客观特征 | `cells.parquet`、`tiles` | `processed/osm_features.parquet` |
| **04** `04_labeling.ipynb` | 用 Gemini（Vertex AI）给每张街景按 8 个维度打 1–5 分，按格子聚合成标签 Y，并导出人工标注表 | `streetview.parquet`、街景图片、`prompts/v1.txt` | `processed/labels_image.parquet`、`labels_cell.parquet`、`labels_meta.json`、`qa/` |
| **05** `05_build_dataset.ipynb` | 合并格子、OSM 特征、标签，过滤，按空间区块划分 train / val / test（带缓冲区和交叉验证折），登记版本 | `cells`、`osm_features`、`labels_cell` | `datasets/{版本}/`（`dataset.parquet`、`train/val/test.parquet`、`schema.json`、`manifest.json`、`DATASET_CARD.md`） |
| **06** `06_embeddings.ipynb` | 用预训练编码器（ResNet-50、DINOv2-S、CLIP ViT-B/16）把每张卫星图变成特征向量并缓存 | `datasets/v1/`、卫星图 | `embeddings/v1/*.parquet`、`meta.json` |
| **07** `07_baselines.ipynb` | B0（均值）、B1（OSM + LightGBM）、M1（冻结编码器 + Ridge）、M3（图像特征 + OSM 融合）；交叉验证选模型，测试集只评估一次 | `datasets/v1/`、`embeddings/v1/` | `results/{run_id}/`、`docs/EXPERIMENTS.md`（实验日志，每次追加一节） |

### 01 · 街景 → 格子

1. 把试点区域（`BBOX`）切成 zoom 14 的 Mapillary 矢量瓦片，下载全部街景点的元数据（位置、拍摄时间、朝向、是否全景、质量分）。不用 Mapillary 的 bbox 搜索接口，因为它在市中心这种街景密集的地方会报"数据太多"，返回条数也不稳定。
2. 过滤：2019 年以后、排除 12–3 月（落叶季会让树荫等分数偏低）、排除全景图、要有朝向。notebook 会打印每一步剩多少张（"漏斗"）。
3. 每张图用 `h3.latlng_to_cell` 算出所在格子（H3 分辨率 10，每格约 130 m 宽）。
4. 每个格子挑最多 4 张**朝向分散**的图（4 个象限各取最新的一张）。
5. 铺出试点区域的完整网格，统计覆盖率：多少格子有街景、多少"合格"（≥ 2 张）。
6. 从合格格子里随机抽 `SAMPLE_SIZE` 个，多线程下载 1024 px 的缩略图，写出格子主表。

波士顿试点区（Boston + Cambridge，3,532 个格子）的实际结果：Mapillary 共 166 万张街景点，过滤后约 10 万张可用，合格格子 **1,127 个（31.9%）**。最大的损失来自"排除冬季"，这是以后放宽条件的第一个选择。

### 02 · 卫星图

读 `cells.parquet`，在 Microsoft Planetary Computer 上查 NAIP（美国农业部航拍，公有领域），给每个格子挑**完整覆盖它的、年份最新的**一幅，裁出以格子中心为中心、边长 154 m 的方块，重采样成 256×256 的 PNG。同一幅影像里的格子一起处理，只打开一次文件。

补充到 `cells.parquet` 的列：`naip_item`、`naip_year`、`tile_path`、`nodata_frac`、`tile_ok` 等。空白像素超过 5% 的图会标为不可用。notebook 还会显示 NAIP 年份与街景拍摄年份的差距，因为差太多时（新建了楼、砍了树）标签和输入会对不上。

### 03 · OSM + WorldCover 特征

OSM 数据来自 Geofabrik 的**本地快照文件**（`raw/osm/massachusetts-latest.osm.pbf`，约 300 MB，第一次运行时自动下载，之后直接读本地），不再向 Overpass 公共服务器请求（它经常拒绝连接或排队很久）。快照是固定的一份，实验可复现；下载日期和文件信息记在 `raw/osm/*.meta.json` 和运行记录里。整个 notebook 约一分钟。

对每个格子，在和卫星图**同样大小的方块**里统计客观地理特征：

- **道路**：按等级分 4 类的长度（主干道、次干道、居住区街道、步道/自行车道）、自行车道长度、平均车道数、平均限速、路口数。
- **OSM 要素**：POI 数量；水体、绿地、建筑的面积占比。
- **WorldCover 土地覆盖**：树、灌木、草地、农田、建成区、水体等各类占比，以及占比最大的类别。

它们有两个用途：作为模型输入 X 的一部分（也是强基线 B1 的全部输入，用来证明卫星图是否提供了 OSM 没有的信息）；以后给路线打分时，水体、绿地、自行车道这类客观维度直接用它们，不需要模型预测。

---

### 04 · VLM 标注（"老师"）

Gemini 看每张街景，按 `prompts/v1.txt` 里的规则，对 8 个维度各打 1–5 分：`traffic_intensity`（交通强度）、`quietness`（安静）、`shade`（树荫）、`naturalness`（自然感）、`scenic_appeal`（景观吸引力）、`historic_character`（历史风貌）、`liveliness`（活力）、`visual_openness`（视野开阔）。同时让它判断这张图能不能用（车内、模糊、夜间等标为不可用）。

- 通过 Vertex AI 调用（走 GCP 账单），输出用结构化 JSON 约束，temperature 为 0。
- 结果按（图片、prompt 版本、模型）**缓存**，重跑或放大样本不会重复调用、不会重复花钱；改打分规则就升版本号，旧标签不会被覆盖。
- 按格子聚合：只用可用图，取均值并归一化到 0–1；可用图少于 2 张的格子标签为缺失（NaN），训练时跳过。
- **图库**：04 会生成单文件 `processed/labels_gallery.html`，用浏览器打开即可浏览所有图片和打分（缩略图嵌入，点击打开原图），支持按可用性、格子、维度分数筛选，按维度排序，按格子分组，并能对比不同 prompt 版本的打分。
- 自带质检：分数分布（防止全挤在 3）、和 OSM / WorldCover 的客观核对，并导出 150 张图和空白打分表，供组员做 **E1（人机一致性）**。

使用前要完成 Vertex AI 的准备（项目、账单、启用 API、`gcloud auth application-default login`、`.env` 里加 `GOOGLE_CLOUD_PROJECT`），清单在 04 第 2 节。

---

### 05 · 构建数据集与划分

把格子表、OSM 特征、标签按 `h3_index` 合成一张"每个格子一行"的 xy 表，过滤（卫星图不可用、没有标签、街景和卫星图年份相差太多、OSM 特征缺失），再划分数据：

- **按空间区块划分，不是随机划分**。相邻格子几乎一样，随机划分会让测试分数虚高。所以先把地图切成 H3 区块（分辨率 8，约 0.7 km²），整块分给 train / val / test；紧挨着测试区块的训练 / 验证格子丢掉一圈作为缓冲，防止边界泄漏。
- 另外给 train+val 的区块分 5 折（`fold` 列），样本少时做分组交叉验证；数据多了不用这一列就行。
- 以后有第二座城市时，设 `TEST_CITIES = ["providence"]`，整座城市当测试集，才是真正的跨城测试。
- **数据集版本是冻结的**：同一个版本号已存在时拒绝覆盖，改了数据或参数就升版本号。`manifest.json` 记录输入文件的哈希、prompt 版本、OSM 快照日期、过滤条件和划分参数；`DATASET_CARD.md` 是自动生成的说明卡。
- 测试集只在最终报告时看一次。

---

### 06 / 07 · 特征提取与基线

**为什么拆成两个 notebook**：PyTorch 和 LightGBM 在 Mac 上不能放进同一个进程（两者各带一份 OpenMP 库，同时加载会崩溃）。所以提特征（只用 torch）和训练基线（只用 LightGBM / sklearn）分开，也对应以后的两个容器。

**06**：预训练编码器是别人在海量图片上训练好的"看图专家"，我们**不训练它**（冻结），只让它把每张卫星图变成一串几百维的数字。每张图做 8 种旋转 / 翻转取平均（俯视图没有方向）。特征缓存后，07 不再碰图片。

**07**：
- **B0** 预测均值；**B1** 只用 OSM / WorldCover 特征（LightGBM）；**M1** 只用卫星图特征（Ridge 线性回归）；**M3** 卫星图特征（PCA 压缩）加 OSM 特征（LightGBM）。
- 编码器、正则强度、LightGBM 参数、PCA 维度全部由 **train+val 上按区块分组的 5 折交叉验证**自动选出；**测试集只在最后评估一次**。
- 指标是逐维度 Spearman，置信区间用按区块的自助法；两个模型的差距也给区间，不含 0 才算真的有差别。
- 关键对照是 **M3 − B1**：卫星图能否提供 OSM 没有的信息。

---

## 数据长什么样

```
data/
├── raw/
│   ├── grid/boston_h3r10.parquet           # 完整网格（含没有街景的格子）
│   ├── osm/massachusetts-latest.osm.pbf    # OSM 快照（Geofabrik，约 300 MB）
│   └── mapillary/
│       ├── metadata/boston/tile_z14_*.parquet   # 街景元数据，按瓦片缓存
│       └── images/{image_id}.jpg                # 街景图片
├── processed/
│   ├── cells.parquet           # 格子主表：每个格子一行
│   ├── streetview.parquet      # 入选的街景：每张图一行
│   ├── tiles/{cell_id}.png     # 卫星图
│   ├── osm_features.parquet    # OSM / WorldCover 特征：每个格子一行
│   ├── labels_image.parquet    # Gemini 对每张街景的打分（缓存，键：图片+prompt版本+模型）
│   ├── labels_cell.parquet     # 每个格子的标签 Y（8 维，0–1；缺失为 NaN）
│   ├── labels_meta.json        # 模型、prompt 版本、token 数、花费
│   ├── labels_gallery.html     # 图库：浏览所有图片和打分
│   └── qa/                     # 人工标注用的图片和空白打分表
├── embeddings/v1/              # 06 提取的卫星图特征（每个编码器一个文件）
├── results/{run_id}/           # 07 的指标、差值、预测、配置
├── datasets/v1/                # 冻结的数据集版本
│   ├── dataset.parquet  train.parquet  val.parquet  test.parquet
│   └── schema.json  manifest.json  DATASET_CARD.md
└── runs/{时间戳}/*_manifest.json    # 每步的运行记录（配置、数量、时间）
```

- **主键是 `h3_index`**。`cell_id` 由它派生（`BOS_<h3_index>`），换范围或样本数都不会变。
- 文件路径都存成**相对数据根目录**的路径，换机器、搬进容器或 GCS 都不用改表。
- 街景（Y 的原料）和卫星图 / OSM 特征（X）通过 `h3_index` 对齐。

---

## 数据来源与许可

| 数据 | 来源 | 许可 / 注意 |
|---|---|---|
| 街景 | Mapillary | CC-BY-SA 4.0；成果中要注明来源，发布派生数据集要看 Share-Alike |
| 卫星图 | NAIP（经 Planetary Computer） | 公有领域 |
| 道路、水体、POI 等 | OpenStreetMap（Geofabrik 每日快照） | ODbL；注明 © OpenStreetMap contributors |
| 土地覆盖 | ESA WorldCover 2021 v200 | CC-BY 4.0 |

只有 Mapillary 需要 token，并且免费；01–03 不需要 GCP，也不产生费用。04 通过 Vertex AI 调用 Gemini，会产生费用（每张图约千分之几美元，具体看型号，04 会估算）。

---

## 接下来

- **E1：人工标注验证标签**（`processed/qa/` 里已有 150 张图和空白打分表）：只对通过的维度下结论。
- **M2 微调**、遥感专用编码器（SatlasPretrain）；放宽冬季过滤的消融；第二座城市（Providence）做真正的跨城测试。
- 把 notebook 整理成 `.py` 并容器化（一条命令跑完整条流程），补 SETUP 文档、DVC、W&B。

## 已知局限

- Mapillary 的矢量瓦片是公开子集，不保证包含全部街景。
- OSM 是某一天的快照；新建的路、新开的店不会出现。换城市时需要换成对应州的 `.pbf`（改 03 配置里的 `OSM_PBF_URL`）。
- 目前只覆盖波士顿一座城市，跨城泛化要等加入第二个城市后才能测。
- 过滤条件（年份、月份、全景、质量分）都是可调参数，改动前先看 01 的漏斗和覆盖率图。
- 全景图暂时排除；以后可以切成 4 个 90° 视角再加回来。

## App (api-service + frontend)

A local web app: type the scenery you want, get a heatmap of matching tiles.
Full docs: [docs/app/README.md](docs/app/README.md)

Quick start (key file goes in `../secrets/`, outside the repo):

    docker compose up --build

- Page: http://localhost:3000
- API docs: http://localhost:9000/docs
- No key? `LLM_PROVIDER=mock docker compose up --build`
