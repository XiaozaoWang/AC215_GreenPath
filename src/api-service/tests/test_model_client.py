# model_client.check_tile_ids（门卫）的单元测试：模型交回来的格子必须和交出去的一一对应。
import pytest

from api import model_client, tiles
from api.dimensions import DIMENSION_NAMES
from api.schemas import Tile, TileScore


# 造 3 个格子（编号随便起，门卫只看编号对不对得上）
TILES = [Tile(h3_index=f"t{i}") for i in range(3)]


# 造一条模型结果（分数随便给，都是 0.5）
def score(h3_index):
    return TileScore(h3_index=h3_index, scores={name: 0.5 for name in DIMENSION_NAMES})


# 一一对应：不报错
def test_matching_tiles_pass():
    model_client.check_tile_ids(TILES, [score(t.h3_index) for t in TILES])


# 少交、多交、重复：都要报错
@pytest.mark.parametrize("returned", [
    [score("t0"), score("t1")],                              # 少了 t2
    [score("t0"), score("t1"), score("t2"), score("t9")],    # 多了 t9
    [score("t0"), score("t0"), score("t1"), score("t2")],    # t0 交了两次
])
def test_mismatch_rejected(returned):
    with pytest.raises(model_client.TileMismatchError):
        model_client.check_tile_ids(TILES, returned)


# 现在的假模型走一遍 predict()：Cambridge 的真实格子，门卫放行
def test_predict_passes_check():
    region = tiles.get_tiles("cambridge")[:3]
    assert len(model_client.predict(region)) == 3
