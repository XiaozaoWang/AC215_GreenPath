# 打分数学 目标为模型原始分 X + 偏好权重 w → 每格总分 t。
# 步骤为区域内 z-score（σ 有地板）→ 截断 → t = Z·w → must_have 门槛 → 每个格子一个总分。
import numpy as np

from api.dimensions import DIMENSION_NAMES
from api.schemas import ScoreStats, TileScore

# σ 地板：差异小于模型误差就不算差异。
# PLACEHOLDER：M2 还没有真模型的每维 RMSE，先统一假设模型平均差 0.05（分数量程 0-1）；
# 以后从模型的 metrics.json 读
PLACEHOLDER_RMSE = 0.05
# z-score 截断范围：防止个别极端格子一家独大
Z_CLIP = 3.0
# must_have 门槛：这一维原始分低于这个值的格子直接出局（0-1 量程，相当于原来的 10 分）
MUST_HAVE_MIN = 0.1


# 将model_client.predict得出的，每一项代表一个格子全部信息的列表
# 转化为 格子数 × 维度数 的矩阵，丢掉编号
def to_matrix(tile_scores: list[TileScore]) -> np.ndarray:
    return np.array([[ts.scores[name] for name in DIMENSION_NAMES] for ts in tile_scores], dtype=float)


# 区域内标准化：每维减去区域均值、除以 σ（σ 不小于 RMSE），再截到 ±Z_CLIP
def standardize(X: np.ndarray, rmse: np.ndarray):
    mu = X.mean(axis=0)
    sigma = np.maximum(X.std(axis=0), rmse)
    Z = np.clip((X - mu) / sigma, -Z_CLIP, Z_CLIP)
    return Z, mu, sigma


# 主函数：原始分 + 权重 → 每个格子的总分（和输入顺序一致，没过门槛的格子是 None）+ 中间数字
def combine(
    tile_scores: list[TileScore],
    weights: dict[str, float],
    must_have: list[str] = (),
    rmse: dict[str, float] | None = None,
) -> tuple[list[float | None], ScoreStats]:
    # 标准化：每维换成"比区域平均高/低几个 σ"
    X = to_matrix(tile_scores)
    rmse_vec = np.array([(rmse or {}).get(name, PLACEHOLDER_RMSE) for name in DIMENSION_NAMES])
    Z, mu, sigma = standardize(X, rmse_vec)

    # 加权求和：t = Z·w，每格一个总分
    w = np.array([weights.get(name, 0.0) for name in DIMENSION_NAMES])
    t = Z @ w

    # must_have 门槛：用原始分把门（不用 Z，因为 Z 只说明相对高低）
    passed = np.ones(len(t), dtype=bool)
    for name in must_have:
        passed &= X[:, DIMENSION_NAMES.index(name)] >= MUST_HAVE_MIN

    # 每个格子一个总分（没过门槛的是 None）
    values = [round(float(value), 3) if ok else None for value, ok in zip(t, passed)]

    # 中间数字：给页面展示、给人检查
    stats = ScoreStats(
        n_tiles=len(t),
        n_excluded=int((~passed).sum()),
        t_min=round(float(t[passed].min()), 3) if passed.any() else None,
        t_max=round(float(t[passed].max()), 3) if passed.any() else None,
        # 0-1 量程下保留 4 位小数（原来 0-100 时保留 2 位）
        mean={name: round(float(m), 4) for name, m in zip(DIMENSION_NAMES, mu)},
        sigma={name: round(float(s), 4) for name, s in zip(DIMENSION_NAMES, sigma)},
    )
    return values, stats
