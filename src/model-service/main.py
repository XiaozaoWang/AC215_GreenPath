"""
模型服务的接口（FastAPI）：部署在 Cloud Run 上一直运行，api-service 调它拿分数。真正打分的代码在 scorer.py。
本地运行后打开 http://localhost:8080/docs，可以在网页上直接试每个接口（Try it out → 选图或填格子 → Execute）。

    GET  /health          服务好了没有、用的是哪个模型
    POST /predict         一批 H3 格子 → 每格 15 个分数（正式用：图从 bucket 里取）
    POST /predict_image   上传一张航拍图（collector 切的那种 256×256 PNG）→ 15 个分数（测试用）
"""
import json
import time
from collections import Counter

from fastapi import FastAPI, UploadFile
from pydantic import BaseModel, Field

import scorer    # import 时就加载好模型和格子索引，之后每个请求直接用

# 一个请求最多几个格子。更大的区域由 api-service 拆成几份同时发：每份交给一个实例，实例越多越快
MAX_CELLS = 2000

app = FastAPI(title="GreenPath model service")


# /predict 收到的请求长这样：{"h3_indices": ["8a2802374807fff", ...]}（res 10 的 H3 编号）
class PredictRequest(BaseModel):
    h3_indices: list[str] = Field(min_length=1, max_length=MAX_CELLS)


# 服务好了没有：Cloud Run、--test 和你都可以用它检查；回答里有模型名，能确认部署的是哪个模型
@app.get("/health")
def health():
    return {"status": "ok", "run_id": scorer.RUN_ID, "training_id": scorer.TRAINING_ID,
            "targets": scorer.TARGETS, "cells": len(scorer.INDEX)}


# 正式接口：格子 → 分数。回答：{"run_id", "results": [{"h3_index", "status", "scores"}, ...]}，顺序和请求一样
@app.post("/predict")
def predict(req: PredictRequest):
    t0 = time.time()
    results = scorer.score_cells(req.h3_indices)
    # 每个请求打一行 JSON 日志（monitoring）：Cloud Run 自动收进 Cloud Logging，可以按字段搜，例如图不能用的格子特别多的请求
    print(json.dumps({"event": "predict", "cells": len(results), "status": Counter(r["status"] for r in results),
                      "seconds": round(time.time() - t0, 2)}), flush=True)
    return {"run_id": scorer.RUN_ID, "results": results}


# 测试接口：直接上传一张图打分，不经过格子索引
@app.post("/predict_image")
def predict_image(file: UploadFile):
    return {"run_id": scorer.RUN_ID, "scores": scorer.score_images([file.file.read()])[0]}
