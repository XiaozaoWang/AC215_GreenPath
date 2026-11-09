# 给一个区域编号，返回这个区域里所有 H3 六边形格子的清单。
# "cambridge" → list[Tile]

from api import fake_model
from api.schemas import Tile


# 对外入口：区域编号 → 格子清单；不认识的编号就报错
# FAKE：现在格子清单来自假分数文件；接上真模型后改成“区域范围 → 覆盖它的 H3 格子”
def get_tiles(region_id: str) -> list[Tile]:
    region = fake_model.load_region(region_id)
    return [Tile(h3_index=h) for h in region["cells"]]
