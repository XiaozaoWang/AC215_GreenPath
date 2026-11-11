# 模型服务对接说明（给 api-service）

model-service 部署在 Cloud Run（us-central1），**私有**：只有授权的服务账号带着 ID token 才能调用。
输入一批 H3 格子编号，返回每格 15 个 0–1 的风景分数。图是服务自己从 bucket 读的，调用方只传编号。

- 服务名：`greenpath-model-service`
- 网址：Cloud Run 控制台 → greenpath-model-service 页面顶部的 URL（`https://greenpath-model-service-….run.app`）。api-service 部署时用环境变量 `MODEL_SERVICE_URL` 传进去，代码里不写死
- 当前配置：CPU 版，每个实例 4 vCPU / 4 GiB，**每个实例同时只处理 1 个请求**，最多 30 个实例，没有请求时为 0 个实例

## 接口

### `POST /predict`（正式用）

请求：

```json
{"h3_indices": ["8a2802374807fff", "8a280237480ffff"]}
```

- H3 **res 10** 的编号（字符串），每个请求 1–2000 个，超过返回 422
- 可以有重复，会照原样返回

返回（`results` 和请求**一一对应、顺序相同**）：

```json
{
  "run_id": "dinov2_s-linear-seed42-ebcc811f",
  "results": [
    {"h3_index": "8a2802374807fff", "status": "ok",
     "scores": {"tree_canopy": 0.9917, "forest_environment": 0.7411, "...": "15 个分数"}},
    {"h3_index": "8a2a1072b59ffff", "status": "not_downloaded", "scores": null}
  ]
}
```

`status`：

| 值 | 意思 | `scores` |
|---|---|---|
| `ok` | 打了分 | 15 个分数，0–1，保留 4 位小数 |
| `not_downloaded` | 这个格子不在已采集的区域里（没有航拍图） | `null` |
| `no_naip_coverage` / `mostly_black` | 有记录，但航拍图不能用（没有 NAIP / 大部分是黑的） | `null` |

**api-service 要处理 `scores` 为 `null` 的格子**（例如不画、显示灰色）。现在 `model_client.check_tile_ids` 要求每个格子都有分数，接上之后要改。

15 个分数的名字（顺序 = `/health` 里的 `targets`）：

```
tree_canopy, forest_environment, grassland, open_landscape, road_width, water_access, urban_density,
street_enclosure, farmland_pastoral, industrial_area, historic_character, commercial_district,
scenic_appeal, architectural_character, visual_clutter
```

和 api-service 的 `dimensions.json`（16 个）比，**少了 `hilly_terrain`**，其余 15 个名字一样。api-service 要去掉 `hilly_terrain`，或者给它一个默认值，不然 `TileScore` 的检查会报错。

### `GET /health`

```json
{"status": "ok", "run_id": "dinov2_s-linear-seed42-ebcc811f", "training_id": "full_v2",
 "targets": ["tree_canopy", "..."], "cells": 25896}
```

`cells` = 能打分的格子数（目前是 1026_Full_data 的 29 个区域）。

### `POST /predict_image`（测试用）

上传一张 256×256 的航拍 PNG（multipart，字段名 `file`），返回 `{"run_id": "...", "scores": {...}}`。

## 速度和拆分（重要）

- CPU 实例**每个约 6 格/秒**（实测：500 格约 80 秒）。一个请求只会落到一个实例上，所以**一大批格子必须拆开、同时发**
- 做法：把一批格子**平均分成 30 份，同时发 30 个 `/predict` 请求**。model-service 每个实例一次只接一个请求，Cloud Run 会自动把 30 个请求分到 30 个实例上并行算，回来后按顺序拼起来
  - 例：900 格 → 30 份 × 30 格，每份约 5 秒
- 份数不要超过 30（实例上限），多出来的请求会排队
- **冷启动**：闲置一段时间后实例数为 0，第一批请求要先启动实例、加载模型，多等十几秒。请求超时设 300 秒

## api-service 部署到 Cloud Run 后怎么连 model-service

1. **api-service 用自己的服务账号运行**，例如 `greenpath-api-service@<项目>.iam.gserviceaccount.com`（部署时 `--service-account` 指定）
2. **给这个服务账号调用 model-service 的权限**（只做一次）：
   控制台 → Cloud Run → greenpath-model-service → **Security**（或 Permissions）→ Add principal → 填上面的服务账号 → 角色 **Cloud Run Invoker** → Save
   或：
   ```sh
   gcloud run services add-iam-policy-binding greenpath-model-service --region us-central1 \
     --member=serviceAccount:greenpath-api-service@<项目>.iam.gserviceaccount.com --role=roles/run.invoker
   ```
3. **部署 api-service 时传网址**：环境变量 `MODEL_SERVICE_URL=https://greenpath-model-service-….run.app`
4. **代码里每次调用带 ID token**：在 Cloud Run 上，`google.oauth2.id_token.fetch_id_token` 会自动用 api-service 的服务账号拿到令牌（audience = model-service 的网址），不需要任何 key 文件

参考代码（替换 `model_client.py` 里取假分数的部分；依赖 `httpx`、`google-auth`、`requests`）：

```python
import math
import os
from concurrent.futures import ThreadPoolExecutor

import google.auth.transport.requests
import google.oauth2.id_token
import httpx

MODEL_URL = os.environ["MODEL_SERVICE_URL"]   # 云上：https://greenpath-model-service-….run.app；本地：http://host.docker.internal:8080
N_PARTS = 30                                   # 拆几份 = 同时用几个实例（不超过 30）


# 私有服务要带身份令牌；本地 http 地址（本地 model-service 或 gcloud run services proxy）不需要
def auth_headers():
    if not MODEL_URL.startswith("https://"):
        return {}
    token = google.oauth2.id_token.fetch_id_token(google.auth.transport.requests.Request(), MODEL_URL)
    return {"Authorization": f"Bearer {token}"}


# 一批格子 → 拆成 N_PARTS 份同时发 → 按原顺序拼回来
# 返回 [{"h3_index", "status", "scores"}, ...]，和输入一一对应
def predict_cells(h3_indices):
    size = math.ceil(len(h3_indices) / N_PARTS)
    parts = [h3_indices[i:i + size] for i in range(0, len(h3_indices), size)]
    headers = auth_headers()

    def call(part):
        r = httpx.post(f"{MODEL_URL}/predict", json={"h3_indices": part}, headers=headers, timeout=300)
        r.raise_for_status()
        return r.json()["results"]

    with ThreadPoolExecutor(len(parts)) as pool:
        return [row for rows in pool.map(call, parts) for row in rows]
```

## 本地开发时

`fetch_id_token` 只在云上（或用服务账号 key）能用，本地用个人 gcloud 登录拿不到。本地两种办法，都把 `MODEL_SERVICE_URL` 设成 http 地址，不带令牌：

- **连云上的服务**：Mac 上运行 `gcloud run services proxy greenpath-model-service --region us-central1 --port 8080`（它替你带身份），`MODEL_SERVICE_URL=http://host.docker.internal:8080`（api-service 在 Docker 里时）
- **连本地的服务**：在 `src/model-service/` 下 `sh docker-shell.sh`（要先在 model-deployment 里 `python cli.py --prepare`），地址同上
