# 自动检查 score_math.py 的打分数学有没有算错。它造几个小例子，比如 4 个格子、水分分别是 0、0.2、0.8、0.4，再断言结果应该怎样，比如"水最多的那格总分最高"。
# score_math.py 的单元测试：纯数学，不调 Gemini、不花钱。
import pytest
from pydantic import ValidationError

from api import score_math
from api.dimensions import DIMENSION_NAMES
from api.schemas import TileScore


# 造 N 个格子的小区域：指定几维的分数，其余维一律 0.5 分（分数量程 0-1）
def make_tiles(**dims: list[float]) -> list[TileScore]:
    n = len(next(iter(dims.values())))
    return [
        TileScore(h3_index=f"t{i}", scores={name: dims[name][i] if name in dims else 0.5 for name in DIMENSION_NAMES})
        for i in range(n)
    ]


# combine 返回的就是每个格子的总分列表（和输入顺序一致）
def first_row(values):
    return values


# 权重全 0 → 每格总分都是 0（没有偏好就没有高低）
def test_zero_weights_give_flat_map():
    heatmap, _ = score_math.combine(make_tiles(water_access=[0, 0.2, 0.8, 0.4]), weights={})
    assert first_row(heatmap) == [0, 0, 0, 0]


# 想要水 → 水分越高的格子总分越高
def test_wanted_dimension_ranks_highest():
    row = first_row(score_math.combine(make_tiles(water_access=[0, 0.2, 0.8, 0.4]), {"water_access": 1})[0])
    assert row.index(max(row)) == 2
    assert row[2] > row[3] > row[1] > row[0]


# 不想要宽马路 → 排名反过来，路最宽的格子总分最低
def test_negative_weight_flips_ranking():
    row = first_row(score_math.combine(make_tiles(road_width=[0, 0.2, 0.8, 0.4]), {"road_width": -1})[0])
    assert row.index(min(row)) == 2


# 均值整体平移不影响结果：z-score 只看"比平均高多少"
def test_mean_shift_does_not_change_result():
    a = first_row(score_math.combine(make_tiles(water_access=[0, 0.2, 0.8, 0.4]), {"water_access": 1})[0])
    b = first_row(score_math.combine(make_tiles(water_access=[0.1, 0.3, 0.9, 0.5]), {"water_access": 1})[0])
    assert a == pytest.approx(b)


# σ 地板：几乎一样的分数（差异 < 模型误差）不会被放大成大差异
def test_sigma_floor_stops_noise_amplification():
    tiles = make_tiles(water_access=[0.5, 0.505, 0.495, 0.5])
    with_floor = first_row(score_math.combine(tiles, {"water_access": 1})[0])
    without_floor = first_row(score_math.combine(tiles, {"water_access": 1}, rmse={"water_access": 0.0})[0])
    assert max(abs(v) for v in with_floor) < 0.2
    assert max(abs(v) for v in without_floor) > 1.0


# 截断：一个极端格子的 Z 最多到 ±3
def test_z_is_clipped():
    row = first_row(score_math.combine(make_tiles(water_access=[0] * 99 + [1.0]), {"water_access": 1})[0])
    assert max(row) == pytest.approx(score_math.Z_CLIP)


# must_have：原始分没到门槛的格子出局（None），并被计数
def test_must_have_excludes_tiles():
    heatmap, stats = score_math.combine(
        make_tiles(water_access=[0, 0.05, 0.15, 0.3]), {"water_access": 1}, must_have=["water_access"]
    )
    assert first_row(heatmap)[:2] == [None, None]
    assert None not in first_row(heatmap)[2:]
    assert stats.n_excluded == 2


# 结果顺序：第几个格子的总分就在第几个位置
def test_values_follow_input_order():
    values, _ = score_math.combine(make_tiles(water_access=[0, 0, 1.0, 0, 0]), {"water_access": 1})
    assert len(values) == 5
    assert values.index(max(values)) == 2


# 量程检查：模型交回来的分数超出 0-1（比如还按 0-100 给的 1.5、80）会被拦下
@pytest.mark.parametrize("bad", [1.5, 80.0, -0.1])
def test_scores_outside_range_rejected(bad):
    with pytest.raises(ValidationError):
        make_tiles(water_access=[bad])
