# 我们通过 tiles.get_tiles 拿到一个区域的 H3 格子清单（list[Tile]）
# 这里通过 predict 函数把每个格子的 16 维分数填进去，并用 check_tile_ids 查验
# FAKE：模型还没部署，先读事先存好的分数（fake_model.py）；接上模型服务后只换 predict 里取分数的那一行

from api import fake_model
from api.schemas import Tile, TileScore


# 模型交回来的结果和交出去的格子对不上时抛这个错（和模型组的契约被打破了）
class TileMismatchError(ValueError):
    pass


# 格子清单 → 每个格子加上 16 维分数
def predict(tiles: list[Tile]) -> list[TileScore]:
    # FAKE：现在从假模型拿分数；真模型接上后改成调模型服务（POST /predict）
    scores = fake_model.fake_scores([t.h3_index for t in tiles])
    tile_scores = [TileScore(h3_index=t.h3_index, scores=scores[t.h3_index]) for t in tiles if t.h3_index in scores]
    check_tile_ids(tiles, tile_scores)
    return tile_scores


# 门卫：交出去的 tiles 和拿回来的 tile_scores 必须一一对应
# 防的是真模型（HTTP）少交、多交、重复交——这种错不报的话热图看着"挺正常"
def check_tile_ids(tiles: list[Tile], tile_scores: list[TileScore]) -> None:
    sent = {t.h3_index for t in tiles}
    got = [ts.h3_index for ts in tile_scores]

    duplicates = sorted({i for i in got if got.count(i) > 1}) if len(got) != len(set(got)) else []
    missing = sorted(sent - set(got))
    extra = sorted(set(got) - sent)

    if duplicates or missing or extra:
        # 每类最多列 5 个例子
        raise TileMismatchError(
            f"model output does not match the tiles sent: "
            f"missing={missing[:5]} ({len(missing)}), extra={extra[:5]} ({len(extra)}), "
            f"duplicated={duplicates[:5]} ({len(duplicates)})"
        )
