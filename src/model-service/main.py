"""
Model Service API (FastAPI). Runs on Cloud Run all the time, api-service calls it to get scores. The actual scoring code is in scorer.py.
Run it locally, then open http://localhost:8080/docs to try each endpoint in the browser (Try it out, pick an image or fill in cells, Execute).

    GET  /health          is the service ready, which model it uses
    POST /predict         a batch of H3 cells in, 15 scores per cell out (the real one: images come from the bucket)
    POST /predict_image   upload one aerial tile (a 256x256 PNG like the collector makes), get 15 scores (for testing)
"""
import json
import time
from collections import Counter

from fastapi import FastAPI, UploadFile
from pydantic import BaseModel, Field

import scorer    # importing it already loads the model and the cell index, then every request just uses them

# Max cells per request. Bigger areas are split by api-service into several parts sent at once: each part goes to one instance, more instances is faster
MAX_CELLS = 2000

app = FastAPI(title="GreenPath model service")


# A /predict request looks like this: {"h3_indices": ["8a2802374807fff", ...]} (res 10 H3 ids)
class PredictRequest(BaseModel):
    h3_indices: list[str] = Field(min_length=1, max_length=MAX_CELLS)


# Is the service ready: Cloud Run, --test and you can all use it to check; the answer has the model name, so you can confirm which model is deployed
@app.get("/health")
def health():
    return {"status": "ok", "run_id": scorer.RUN_ID, "training_id": scorer.TRAINING_ID,
            "targets": scorer.TARGETS, "cells": len(scorer.INDEX)}


# The real endpoint: cells in, scores out. Answer: {"run_id", "results": [{"h3_index", "status", "scores"}, ...]}, same order as the request
@app.post("/predict")
def predict(req: PredictRequest):
    t0 = time.time()
    results = scorer.score_cells(req.h3_indices)
    # One JSON log line per request (monitoring): Cloud Run puts it into Cloud Logging automatically, searchable by field, e.g. requests with a lot of cells whose image can't be used
    print(json.dumps({"event": "predict", "cells": len(results), "status": Counter(r["status"] for r in results),
                      "seconds": round(time.time() - t0, 2)}), flush=True)
    return {"run_id": scorer.RUN_ID, "results": results}


# Test endpoint: upload one image and score it directly, skips the cell index
@app.post("/predict_image")
def predict_image(file: UploadFile):
    return {"run_id": scorer.RUN_ID, "scores": scorer.score_images([file.file.read()])[0]}
