# 风景维度表的"读取器"：真正的定义在同一个文件夹的 dimensions.json（全组共用的一份规定），这里只负责读它。
# 别的文件照旧 import DIMENSIONS / DIMENSION_NAMES，用法不变。
# 改维度只改 dimensions.json；改完假分数 data/fake/cambridge_scores.json 也要跟着换。
import json
from pathlib import Path

# 读 dimensions.json（和本文件放在同一个文件夹）
_SPEC = json.loads(Path(__file__).with_name("dimensions.json").read_text(encoding="utf-8"))

# 维度表的版本号：改了维度就升版本，标签和模型都要记下自己用的是哪一版，schema_version来自json
SCHEMA_VERSION = _SPEC["schema_version"]

# 模型输出分数的量程（全组约定 0-1）：schemas.py 用它校验模型交回来的分数，score_range来自json
SCORE_MIN, SCORE_MAX = _SPEC["score_range"]

# 真正会发给Gemini的维度名和维度说明
DIMENSIONS = {name: spec["description"] for name, spec in _SPEC["dimensions"].items()}

# 只要名字的列表，给别的文件用
DIMENSION_NAMES = list(DIMENSIONS)
