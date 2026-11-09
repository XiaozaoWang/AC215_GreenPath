# 假模型：读事先存好的 Cambridge / Charles River 分数（847 个 H3 格子 × 16 维，0-1）
# 分数来自 1026_Full_data 数据集里这片区域的真实 a5/v7 标签（街景按 10/31 定的规则换算、加权后和航拍合并）
# 接入真模型之后删掉

import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "fake"

# 有哪些区域：编号 → 分数文件
REGION_FILES = {"cambridge": DATA_DIR / "cambridge_scores.json"}

# 读一次就缓存起来，之后的请求直接用
_cache: dict[str, dict] = {}


# 读一个区域：{"region_id", "label", "cells": {h3 编号: {维度: 分数}}}；不认识的编号报错
def load_region(region_id: str) -> dict:
    if region_id not in REGION_FILES:
        raise ValueError(f"unknown region_id {region_id!r}, available: {list(REGION_FILES)}")
    if region_id not in _cache:
        _cache[region_id] = json.loads(REGION_FILES[region_id].read_text(encoding="utf-8"))
    return _cache[region_id]


# 对外的函数：一批 H3 编号 → 每个格子的 16 维分数（在已读过的区域里查）
def fake_scores(h3_indexes: list[str]) -> dict[str, dict[str, float]]:
    all_cells = {h: s for region in REGION_FILES for h, s in load_region(region)["cells"].items()}
    return {h: all_cells[h] for h in h3_indexes if h in all_cells}
