"""
GreenPath数据处理读：data-labeler 打好的分，做成训练用的数据表。
两种用法：
1.python cli.py --prepare （做完整的训练数据准备：1.分 Train/Val/Test 2.删掉可疑格子（三条规则 + 人工审核） 3.街景校准到航拍尺度  4.融合成 15 个目标
输出一个新文件夹：dataset.parquet（训练表）、exclusions.parquet（删了哪些格子、为什么）、meta.json
2.python cli.py                  只做检测：三条规则找出可疑的图片 / 格子，输出 candidates.parquet，不删任何东西

常用参数：
  --bucket / 环境变量 GREENPATH_BUCKET     读哪个 bucket（默认数据集 datasets/1026_Full_data）
  --input-root 本地文件夹                   改读本地的冻结快照（文件夹结构和 bucket 里的数据集一样）
  --training-id / --output-dir              结果默认写到 gs://<bucket>/training/<dataset-id>_<时间>/；没有 bucket 时写本地 outputs/dev/

保护：输出目录已存在就报错（旧的训练集永远不会被覆盖），也不允许写进 datasets/ 下面。
"""
import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import io
import itertools
import json
import logging
import os
from pathlib import Path
import re
import uuid

import gcsfs
import h3
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

LOG = logging.getLogger(__name__)


# ==================== 1.读数据函数定义 ====================

class Inputs:
    """数据集根目录：gs://... 或本地文件夹，下面是 cells/、labels/ 等，每个区域一个 Parquet。"""

    def __init__(self, root: str):#运行 python cli.py --prepare 时，cli.py:781 先拼出数据集地址，这里记下root地址，同时如果gs://开头，准备一个能读云上文件的工具，存进 self.fs；
        self.root = root.rstrip("/")
        self.fs = gcsfs.GCSFileSystem(token="google_default") if root.startswith("gs://") else None

    def metadata(self, folder: str) -> dict: #json 的快捷方式：读某个文件夹里的 meta.json
        return self.json(f"{folder}/meta.json")

    # 把一个文件夹（cells/，streetview/，labels/aerial_a5_gemini-2.5-flash/cell_labels/等等层级）里所有区域的 Parquet 拼成一张表，并加上 area_id 列（= 文件名）
    def areas(self, folder: str, columns: list[str] | None = None) -> pd.DataFrame:
        path = f"{self.root}/{folder}"
        files = self.files(f"{folder}/*.parquet")
        if not files:
            raise FileNotFoundError(f"No area Parquet files in {path}")
        parts = []
        for file in files:
            if self.fs:
                with self.fs.open(file, "rb") as stream:
                    part = pd.read_parquet(stream, columns=columns)
            else:
                part = pd.read_parquet(file, columns=columns)
            part["area_id"] = Path(file).stem
            parts.append(part)
        return pd.concat(parts, ignore_index=True)

    #读一个 JSON 文件
    def json(self, relative: str) -> dict:
        path = f"{self.root}/{relative}"
        if self.fs:
            with self.fs.open(path) as stream:
                return json.load(stream)
        return json.loads(Path(path).read_text(encoding="utf-8"))

    #列出符合条件的文件，按名字排好序。areas 靠它找到那 29 个文件
    def files(self, pattern: str) -> list:
        return sorted(self.fs.glob(f"{self.root}/{pattern}") if self.fs else Path(self.root).glob(pattern))


# 它读入人工审核标记的那个 CSV，返回表格，以及文件的原始内容用于算hash（方便以后能回答"这个训练集是用哪一版审核标记做出来的"）。
def read_flags(path: str) -> tuple[pd.DataFrame, bytes]:
    if str(path).startswith("gs://"):
        with gcsfs.GCSFileSystem(token="google_default").open(path, "rb") as stream:
            data = stream.read()
    else:
        data = Path(path).read_bytes()
    return pd.read_csv(io.BytesIO(data)), data


# ==================== 规则 1：街景照片是不是在室内 / 隧道 / 桥底拍的 ====================
# 不看图片，只看 Gemini 打分时写的说明（rationale）里的词句
'''
这里建立了一个涵盖不同类型词性的词表（可以用于拼成一个句子）
phrases()函数自动把词表翻译为正则。长词排在前面避免NEGATED_OBSTRUCTION删除完之后留一个s
EXCLUDE说明这张照片视线被挡住，很可疑；--prepare 时它所在的格子整个删掉
KEEP说明有证据显示虽然提到了隧道、桥，但人在外面、视野开阔，保留
什么都没提到，默认Keep

classify_street对每张图的每句说明都走同样的4步：
去掉否定NEGATED_OBSTRUCTION（删除只对 EXCLUDE 生效，KEEP 看的是原句）
看有没有 KEEP 证据，记下来；
按顺序试 9 条 EXCLUDE：对上强证据：直接 EXCLUDE；对上弱证据：如果有 KEEP 证据就 KEEP，否则 EXCLUDE。一条都没对上：KEEP（默认相信这张照片）。
'''

def phrases(words: list[str]) -> str:
    """把词表变成正则：长的词组排前面（先匹配 "parking garages" 再匹配 "parking garage"），空格可匹配任意空白。"""
    patterns = [r"\s+".join(re.escape(word) for word in phrase.split())
                for phrase in sorted(words, key=len, reverse=True)]
    return "(?:" + "|".join(patterns) + ")"


# 词表：像普通单词一样改，复数要自己写上。停车场（parking lot）故意不算；只提到建筑本身也不算
SPACE_WORDS = [
    "tunnel", "tunnels", "garage", "garages",
    "parking garage", "parking garages", "parking structure", "parking structures",
    "parking deck", "parking decks", "car park", "car parks", "underpass", "underpasses",
]
STRUCTURE_WORDS = SPACE_WORDS + [
    "overpass", "overpasses", "bridge", "bridges", "highway", "highways",
    "freeway", "freeways", "elevated infrastructure",
]
ENCLOSED_PLACE_WORDS = SPACE_WORDS + [
    "store", "stores", "shop", "shops", "room", "rooms", "building", "buildings",
    "mall", "malls", "warehouse", "warehouses", "enclosed space", "enclosed spaces",
    "enclosed structure", "enclosed structures",
]
ARTICLE_WORDS = ["a", "an", "the"]
MODIFIER_WORDS = ["enclosed", "indoor", "underground", "covered"]
LOCATION_WORDS = ["inside", "inside of", "within", "in the interior of", "in"]
VIEWPOINT_WORDS = ["camera", "viewpoint", "perspective", "image", "photo", "photograph", "scene", "view", "shot"]
STATE_WORDS = ["is", "was", "appears", "appears to be", "seems", "seems to be"]
CAPTURE_WORDS = ["taken", "captured", "photographed", "positioned", "located", "situated", "shot"]
# 补充隧道检查用的词（整词匹配，不加复数）
TUNNEL_WORDS = ["tunnel", "garage", "underpass", "parking structure", "parking deck", "parking garage"]
ENCLOSURE_WORDS = ["enclosed", "ceiling", "dark", "concrete walls", "interior", "inside", "dominated by"]
TREE_TUNNEL_PHRASES = ["tunnel-like", "tunnel of", "tunnel effect"]
OBSTACLE_WORDS = [
    "tunnel wall", "tunnel walls", "ceiling", "ceilings", "concrete ceiling", "concrete ceilings",
    "structural elements", "overhead concrete", "overhead structure", "overhead structures", "overhead roof",
] + [
    f"underside of {article}{structure}"
    for article in ["", "a ", "an ", "the "]
    for structure in ["overpass", "bridge"]
]

# 把词表变成匹配器；只有句子结构的部分需要写正则
#\s+：中间隔着空白；\b：必须是完整的单词边界，garage 不会误匹配到 garages 的一半
SPACE = phrases(SPACE_WORDS)
STRUCTURE = phrases(STRUCTURE_WORDS)
ENCLOSED_PLACE = phrases(ENCLOSED_PLACE_WORDS)
ARTICLE = rf"(?:{phrases(ARTICLE_WORDS)}\s+)?(?:{phrases(MODIFIER_WORDS)}\s+)*"
LOCATION = rf"{phrases(LOCATION_WORDS)}\s+{ARTICLE}{ENCLOSED_PLACE}"
VIEWPOINT = phrases(VIEWPOINT_WORDS)
STATE = rf"(?:{phrases(STATE_WORDS)}\s+)?"
CAPTURE = rf"(?:{phrases(CAPTURE_WORDS)}\s+)?(?:from\s+)?"
OBSTACLE = phrases(OBSTACLE_WORDS)

FLAGS = re.IGNORECASE | re.VERBOSE

# 否定说法（"没有被遮挡"、"不是在车库里拍的"）：先删掉，免得被当成证据
NEGATED_OBSTRUCTION = re.compile(r"""
    \b(?:do(?:es)?\s+not|doesn't|don't|never)\s+(?:dominate|obscure|obstruct|block)
    | \b(?:not|never)\s+(?:(?:heavily|severely|substantially)\s+)?
        (?:obstructed|blocked|enclosed|indoors?)
    | \bno\s+(?:significant\s+|substantial\s+)?(?:obstruction|enclosure)
""", FLAGS)
NEGATED_EVIDENCE = re.compile(
    rf"\b(?:not|never)\s+(?:actually\s+)?{CAPTURE}{LOCATION}|{NEGATED_OBSTRUCTION.pattern}",
    FLAGS,
)

# 每条是 (原因, 正则, 能否被 KEEP 证据推翻)
# 前面6条是直接证据（在室内、被结构挡住），直接删除；后面3条不确定，出现明确的 KEEP 证据就保留
EXCLUDE = [
    ("explicit indoor viewpoint", rf"""
        \b(?:{VIEWPOINT}|this|it)\s+{STATE}{CAPTURE}(?:indoors|(?:an?\s+)?indoor\b)
        | \b(?:taken|captured|photographed)\s+indoors\b
    """, False),
    ("camera inside enclosed space", rf"\b{VIEWPOINT}\s+{STATE}{CAPTURE}{LOCATION}\b", False),
    ("interior scene depicted", rf"""
        \b{VIEWPOINT}\s+(?:shows?|depicts?)\s+{ARTICLE}
        (?:(?:view\s+from|road|street)\s+)?(?:inside(?:\s+of)?|interior\s+of)
        \s+{ARTICLE}{ENCLOSED_PLACE}\b
    """, False),
    ("captured inside enclosed space", rf"""
        \b(?:taken|captured|photographed|looking)\s+(?:from\s+)?{LOCATION}\b
    """, False),
    ("structure dominates or hides outdoor view", rf"""
        \b{OBSTACLE}\b[^.!?;]{{0,70}}\b
        (?:dominat(?:e|es|ing)|(?:obscur\w*|obstruct\w*|block\w*|cover\w*)
        \s+(?:almost\s+|nearly\s+)?(?:most|much|the\s+majority))\b
    """, False),
    ("severe structural obstruction", rf"""
        \b(?:view|scene|image|streetscape|environment)\s+{STATE}
        (?:dominated|(?:heavily|severely|substantially|mostly|largely)\s+
        (?:obstructed|blocked|obscured|dominated))\s+by\s+(?:the\s+)?{OBSTACLE}\b
    """, False),
    ("possible enclosed viewpoint", rf"\b{LOCATION}\b|\bscene\s+is\s+{ARTICLE}{SPACE}\b", True),
    ("beneath infrastructure without visibility evidence", rf"""
        (?:\b{VIEWPOINT}\s+{STATE}{CAPTURE}|\bfrom\s+|^)
        (?:under(?:neath)?|beneath|below)\s+(?:(?:a|an|the)\s+)?(?:elevated\s+)?
        (?:overpass(?:es)?|bridges?|highways?|freeways?|infrastructure)\b
    """, True),
    ("interior or overhead structure without visibility evidence", rf"""
        \b{SPACE}\s+(?:interior|walls?|ceilings?)\b
        | \b(?:indoor|enclosed|underground)\s+(?:parking\s+(?:garage|structure|deck)|car\s+park|scene|viewpoint)\b
        | \b(?:ceiling|roof)\s+(?:directly\s+)?(?:overhead|above\s+(?:the\s+)?(?:camera|viewpoint))\b
    """, True),
]


KEEP = [
    ("explicitly negated obstruction", NEGATED_OBSTRUCTION.pattern),
    ("structure nearby or approached", rf"""
        \b(?:near(?:by)?|towards?|approaching|leading\s+(?:to|towards?))\s+{ARTICLE}{STRUCTURE}\b
    """),
    ("structure or entrance ahead/in background", rf"""
        \b{STRUCTURE}\b[^.!?;]{{0,65}}\b(?:ahead|in\s+the\s+(?:distance|background)|nearby)
        | \b(?:entrance|entry)\s+(?:to|of)\s+{ARTICLE}{SPACE}\b
    """),
    ("outdoor streetscape remains visible", r"""
        \b(?:outdoor|surrounding)\s+(?:streetscape|streets?|roads?|buildings?|environment|scene)
        [^.!?;]{0,90}\b(?:visible|unobstructed|clear) | \bopen\s+(?:outdoor\s+)?view\b
    """),
    ("no substantial obstruction", r"""
        \bwithout\s+(?:(?:substantial|significant|major|severe)\s+)?(?:obstruction|obstructing|blocking)
        | \bno\s+(?:significant\s+|substantial\s+)?(?:obstruction|enclosure)
    """),
]
EXCLUDE = [(reason, re.compile(pattern, FLAGS), allow_keep_override)
           for reason, pattern, allow_keep_override in EXCLUDE]
KEEP = [(reason, re.compile(pattern, FLAGS)) for reason, pattern in KEEP]


# 一条说明 → ("EXCLUDE" / "KEEP", 原因)
def classify_street(rationale: str | None) -> tuple[str, str]:
    text = "" if pd.isna(rationale) else re.sub(r"\s+", " ", str(rationale)).strip()
    # 只删掉否定的那一小段，别处的正面证据还在
    positive_text = NEGATED_EVIDENCE.sub(" ", text)
    keep_reason = None
    for reason, pattern in KEEP:
        if pattern.search(text):
            keep_reason = reason
            break
    for reason, pattern, allow_keep_override in EXCLUDE:
        if pattern.search(positive_text):
            if allow_keep_override and keep_reason:
                return "KEEP", keep_reason
            return "EXCLUDE", reason
    if keep_reason:
        return "KEEP", keep_reason
    reason = ("negated enclosed viewpoint" if NEGATED_EVIDENCE.search(text)
              else "no enclosed or obstructed viewpoint evidence")
    return "KEEP", reason


def street_candidates(images: pd.DataFrame) -> pd.DataFrame:
    """规则 1：上面的句子判断为 EXCLUDE，或者符合补充隧道检查（Gemini 说能用，但说明里有隧道词 + 封闭词）。"""
    #检查A：每条说明交给 classify_street 结果是 EXCLUDE 就算命中。
    context = pd.DataFrame(
        images.rationale.map(classify_street).tolist(),
        index=images.index, columns=["decision", "reason"],
    )
    text = images.rationale.fillna("")
    
    #检查B：多个条件同时满足才算可疑：usable=true，说明里有隧道、封闭感的词，同时没有树荫隧道tunnel-like、tunnel of、tunnel effect的说法
    tunnel_places = r"\b(?:" + "|".join(map(re.escape, TUNNEL_WORDS)) + r")\b"
    tunnel = (images.usable.astype(bool)
              & text.str.contains(tunnel_places, case=False)
              & text.str.contains("|".join(map(re.escape, ENCLOSURE_WORDS)), case=False)
              & ~text.str.contains("|".join(map(re.escape, TREE_TUNNEL_PHRASES)), case=False))
    excluded = context.decision.eq("EXCLUDE")
    selected = excluded | tunnel
    result = images.loc[selected].copy()

    # 把检查 A（classify_street 判 EXCLUDE）和检查 B 取并集（selected = excluded | tunnel），然后写原因、加标记列
    # 一张照片只出现一次；两个条件都符合就把两个原因都写上
    reason = context.reason.copy()
    tunnel_reason = "Gemini-usable Street photo: tunnel/garage/underpass plus enclosure wording (extra tunnel check)."
    reason.loc[tunnel & ~excluded] = tunnel_reason
    reason.loc[tunnel & excluded] += "; " + tunnel_reason
    result["matched_context"] = excluded.loc[selected]
    result["matched_tunnel"] = tunnel.loc[selected]
    result["reason"] = reason.loc[selected]
    result["candidate_type"] = "street_scene_keyword"
    result["source"] = "street"
    result["score_scale"] = "1-5 (0 = unusable)"
    return result


# ==================== 规则 2：航拍图坏了 / 被看错了 ====================

def aerial_candidates(tiles: pd.DataFrame, dimensions: list[str]) -> pd.DataFrame:
    """规则 2：所有维度都是最低分，或者"看错"条件（至少 14 个 1 分但 open_landscape 是 5 分）。逐张图做两个检查，任意一个命中就选中"""
    scores = tiles[dimensions]
    minimum = (scores.eq(1) | scores.eq(0) | scores.isna()).all(axis=1) & scores.eq(1).any(axis=1) #每一项都是 1、0 或空，并且至少有一个真的 1
    misread = scores.eq(1).sum(axis=1).ge(14) & tiles.open_landscape.eq(5)
    result = tiles.loc[minimum | misread].copy()#tile有 25,896行，这里指挑选命中行在内存里复制一张小表
    #往里面新加 4 列。
    result["candidate_type"] = np.where(minimum.loc[result.index], "aerial_all_minimum", "misread_aerial")
    result["reason"] = np.where(
        minimum.loc[result.index],
        "Every available raw aerial score is 1; at least one is available.",
        "At least 14 raw scores are 1 and open_landscape is 5 (misread aerial check).",
    )
    result["source"] = "aerial"
    result["score_scale"] = "1-5 (0 = unavailable)"
    return result


# ==================== 规则 3：航拍分数和周围邻居差太多 ====================

@dataclass(frozen=True)
class SpatialConfig:
    dimensions: tuple[str, ...] = ("farmland_pastoral", "grassland")
    low_score: float = 0.25          # 自己 ≤ 2 分才算"低"
    high_neighbor: float = 0.75      # 邻居的中位数 ≥ 4 分才算"高"
    min_difference: float = 0.75     # 邻居比自己高出至少 3 分（最低分为1，所以差值上0.75=3分）
    min_neighbors: int = 4           # 6 个邻居里至少 4 个有分，太少不判
    agreement: float = 0.75          # 至少 75% 的邻居和中位数相差不超过 1 分，也就是邻居们意见一致
    tolerance: float = 0.25          # 至少 75% 的邻居和中位数相差不超过 1 分，也就是邻居们意见一致

SUBURB_ONLY = {"farmland_pastoral", "grassland"}
SPATIAL_COLUMNS = [
    "area_id", "cell_id", "h3_index", "tile_path", "dimension", "original_score",
    "neighbor_median", "score_difference", "n_neighbors", "neighbor_agreement",
    "candidate_type", "source", "score_scale", "reason",
] #规则3另外新建一张小表，只记被抓到的格子，SPATIAL_COLUMNS 就是这张小表的表头。


def spatial_candidates(cells: pd.DataFrame, config: SpatialConfig) -> pd.DataFrame:
    # 每个 H3 位置一个分数，跨区域边界的邻居也算；groupby.first 保持 notebook 里"按文件顺序第一个非空值"
    scores = cells.groupby("h3_index", sort=False)[list(config.dimensions)].first().astype(float)
    records = []
    for row in cells.itertuples(index=False):
        ring = set(h3.grid_disk(row.h3_index, 1)) - {row.h3_index}
        neighbors = scores.reindex(sorted(ring)) #用 h3.grid_disk(位置, 1) 找出紧挨着的 6 个邻居，拿到它们的分；
        for dimension in config.dimensions:
            if dimension in SUBURB_ONLY and row.area_type != "suburb": #不是郊区就跳过。城市里没有农田和草地是正常的，不能算错；
                continue
            target = float(getattr(row, dimension))
            if not np.isfinite(target) or target > config.low_score: #自己的分 > 2 分就跳过，只查"低"的；
                continue
            values = neighbors[dimension].to_numpy(float)
            values = values[np.isfinite(values)]  # 0 分是有效分
            if len(values) < config.min_neighbors: #有分的邻居不到 4 个就跳过；
                continue
            median = float(np.median(values))
            difference = median - target  # 有方向：只抓"自己低、邻居高"
            agreement = float(np.mean(np.abs(values - median) <= config.tolerance)) #算邻居的中位数、差距、一致比例。
            if (median >= config.high_neighbor and difference >= config.min_difference
                    and agreement >= config.agreement):
                #三个条件都满足就记一条：中位数 ≥ 4 分、差距 ≥ 3 分、邻居意见一致。
                records.append({
                    "area_id": row.area_id, "cell_id": row.cell_id, "h3_index": row.h3_index,
                    "tile_path": row.tile_path, "dimension": dimension, "original_score": target,
                    "neighbor_median": median, "score_difference": difference,
                    "n_neighbors": len(values), "neighbor_agreement": agreement,
                    "candidate_type": "spatial_outlier", "source": "aerial", "score_scale": "0-1",
                    "reason": "Low aerial score surrounded by consistently high-scoring H3 neighbors.",
                })
    return pd.DataFrame(records, columns=SPATIAL_COLUMNS)


# ==================== 只做检测（不带 --prepare）====================

#准备材料
def load_inputs(root: str, street_id: str, aerial_id: str, spatial: SpatialConfig):
    # 确定去哪读：数据集根目录，以及两个标签文件夹的名字
    # root 默认是 gs://ac215-notebook/datasets/1026_Full_data
    source = Inputs(root)
    street_folder, aerial_folder = f"labels/{street_id}", f"labels/{aerial_id}"

    # 读出两套标签各有哪16项，代码不写死维度名，以 labeler 的记录为准；对不上就马上报错
    street_dims = source.metadata(street_folder)["dimensions"]
    aerial_dims = source.metadata(aerial_folder)["dimensions"]
    if not street_dims or not aerial_dims or set(spatial.dimensions) - set(aerial_dims):
        raise ValueError("Missing scoring dimensions or unsupported spatial dimensions.")

    # 读5张表 cells/、streetview/ 由 collector 写；image_labels/、tile_labels/、cell_labels/ 由 labeler 写。
    # areas() 会把各区域文件拼起来，并加一列 area_id
    cells = source.areas("cells", ["cell_id", "h3_index", "tile_path", "area_type"])
    streetview = source.areas("streetview", ["image_id", "cell_id", "h3_index", "image_path"])
    street_raw = source.areas(f"{street_folder}/image_labels", ["image_id", "usable", "rationale", *street_dims])
    aerial_raw = source.areas(f"{aerial_folder}/tile_labels", ["cell_id", "rationale", *aerial_dims])
    aerial_normalized = source.areas(f"{aerial_folder}/cell_labels", ["cell_id", *spatial.dimensions])

    # 拼成3份材料
    street = streetview.merge(street_raw, on=["area_id", "image_id"], how="inner", validate="many_to_one") #给规则1的材料，照片属于哪个格子 + Gemini 对这张照片的说明
    keys = ["area_id", "cell_id"]
    tiles = aerial_raw.merge(cells.drop(columns="area_type"), on=keys, how="left", validate="one_to_one") #给规则2的材料，航拍原始分 + 位置和图片路径 → 给规则 2
    aerial = aerial_normalized.merge(cells, on=keys, how="left", validate="one_to_one") #给规则3的材料，航拍格子分 + 位置和城市/郊区

    #完整性检查，每个航拍分都要能找到它的格子信息；每张照片都要有路径，打日志、返回交给detect
    if tiles.tile_path.isna().any() or aerial.h3_index.isna().any() or aerial.area_type.isna().any():
        raise ValueError("Aerial labels do not resolve to complete cell metadata.")
    if street.image_path.isna().any():
        raise ValueError("Street image paths are missing.")
    LOG.info("Loaded %s street relationships and %s aerial cells across %s areas",
             len(street), len(aerial), cells.area_id.nunique())
    return street, tiles, aerial, street_dims, aerial_dims

# 跑规则1，2，3，合成一张清单
def detect(street: pd.DataFrame, tiles: pd.DataFrame, cells: pd.DataFrame,
           street_dimensions: list[str], aerial_dimensions: list[str],
           config: SpatialConfig) -> pd.DataFrame:

    # 三条规则各交出一张小表，只含被抓到的行
    frames = [street_candidates(street), aerial_candidates(tiles, aerial_dimensions),
              spatial_candidates(cells, config)] #load_inputs做出来的aerial，到了detect里面换了个名字叫cells。
    # 把街景和航拍的维度名合在一起，去重，保持顺序
    raw_dimensions = list(dict.fromkeys([*street_dimensions, *aerial_dimensions])) 

    # 原始 1–5 分加 raw__ 前缀，和规则 3 的 0–1 分（original_score）区分开
    for frame in frames:
        frame.rename(columns={d: f"raw__{d}" for d in raw_dimensions}, inplace=True)
        if len(frame) and frame.source.eq("aerial").all():
            frame["image_id"] = frame.cell_id
            frame["image_path"] = frame.tile_path

    # 统一表头，拼成一张，对齐三张小表的列
    columns = [
        "candidate_type", "source", "area_id", "cell_id", "h3_index", "image_id", "image_path",
        "dimension", "reason", "rationale", "score_scale", "original_score", "neighbor_median",
        "score_difference", "n_neighbors", "neighbor_agreement",
        *[f"raw__{d}" for d in raw_dimensions],
    ]
    result = pd.concat([frame.reindex(columns=columns) for frame in frames], ignore_index=True)
    text_columns = [
        "candidate_type", "source", "area_id", "cell_id", "h3_index", "image_id", "image_path",
        "dimension", "reason", "rationale", "score_scale",
    ]

    # 文字列统一成文字；数字列统一成允许空值的数字；邻居数是整数
    result[text_columns] = result[text_columns].astype("string")
    for column in set(columns) - set(text_columns) - {"n_neighbors"}:
        result[column] = result[column].astype("Float64")
    result["n_neighbors"] = result.n_neighbors.astype("Int64")
    return result

#这部分 --prepare 不会运行
def write_candidates(candidates: pd.DataFrame, output: str, metadata: dict) -> None:
    table = pa.Table.from_pandas(candidates, preserve_index=False)
    table = table.replace_schema_metadata({
        **(table.schema.metadata or {}),
        b"greenpath.detection": json.dumps(metadata, sort_keys=True).encode(),
    })
    if output.startswith("gs://"):
        # 写到 bucket 必须显式指定路径，而且不能在 datasets/ 下面（原始数据不动）
        if "datasets" in output.removeprefix("gs://").split("/")[1:]:
            raise ValueError("Write candidates outside datasets/; source artifacts must remain untouched.")
        fs = gcsfs.GCSFileSystem(token="google_default")
        if fs.exists(output):
            raise FileExistsError(f"Output already exists: {output}. Choose a new run path.")
        with fs.open(output, "wb") as stream:
            pq.write_table(table, stream, compression="zstd")
    else:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            pq.write_table(table, stream, compression="zstd")


# ==================== 训练数据准备（--prepare）====================
'''
prepare_dataset 按顺序跑 7 步（全部在内存里做，只有第 7 步写文件）：
第 1 步 load_preparation_inputs：从 bucket 读 collector / labeler 写好的 8 份表，检查完整、对得上
第 2 步 assign_splits：先分好 Train / Val / Test（删格子之前分，分组不受删除影响）
第 3 步 detect + load_manual_flags + collect_exclusions：第 1 轮三条规则、第 2 轮人工审核，命中的格子整个删掉
第 4 步 fuse_scores：街景分换算到航拍尺度（只用 Train + Val 学换算曲线），再按 WEIGHTS 融合成 15 个目标
第 5 步 assemble_table：拼成训练表（每个格子一行，87 列）
第 6 步 validate_table + build_metadata：检查目标在 0–1，写说明书 meta.json 的内容
第 7 步 write_prepared：写出 dataset.parquet / exclusions.parquet / meta.json 到一个新文件夹
'''

# 融合时街景占的权重（其余是航拍）。来源：1000 个格子盲评定下来的；"量多不多"听航拍（tree 0.1），"好不好看"听街景（architectural 0.7）
WEIGHTS = {
    "tree_canopy": 0.1, "forest_environment": 0.4, "grassland": 0.3,
    "open_landscape": 0.3, "road_width": 0.3, "water_access": 0.0,
    "urban_density": 0.1, "street_enclosure": 0.2, "farmland_pastoral": 0.2,
    "industrial_area": 0.5, "historic_character": 0.6, "commercial_district": 0.6,
    "scenic_appeal": 0.6, "architectural_character": 0.7,
}
STREET_ONLY = ["visual_clutter"]          # 只用街景（航拍看不出杂乱）
UNUSED = ["hilly_terrain"]                # 不用（航拍看不出坡）
TARGETS = list(WEIGHTS) + STREET_ONLY     # 15 个目标 = 14 个融合的 + visual_clutter


# 这一版训练集的设置；main 里用命令行参数（dataset-id、street-id、aerial-id、training-id）建它，其余用这里的默认值
@dataclass(frozen=True)
class PreparationConfig:
    dataset_id: str = "1026_Full_data"                 # 读 bucket 里 datasets/ 下哪个数据集
    street_id: str = "street_v7_gemini-2.5-flash"      # 街景标签文件夹 labels/<street_id>/
    aerial_id: str = "aerial_a5_gemini-2.5-flash"      # 航拍标签文件夹 labels/<aerial_id>/
    seed: int = 42                                     # 分组用的哈希种子：同一个种子每次分得一模一样
    # 整个留作 Test 的地点（离它 3 km 内的区域一起留出）
    test_areas: tuple[str, ...] = (
        "city_ma_boston_big", "sub_co_palisade_farm",
        "city_ny_ithaca_mid", "city_ca_santacruz_mid",
    )
    adjacent_km: float = 3          # 两个区域边框相距 < 3 km 就算同一个地点
    block_res: int = 8              # 按 H3 第 8 级大六边形分 Train / Val（一个大六边形约 0.7 km²，包含很多小格子）
    val_share: float = 0.15         # 约 15% 的大六边形分给 Val
    n_folds: int = 5                # Train + Val 再分 5 折（交叉验证用）
    training_id: str = "full_v2"    # 这版训练集的名字（main 会传进来，默认 <dataset-id>_<时间>）

    def notebook_config(self):
        # 写进 meta.json 的设置记录：分组、权重、换算方法、删除规则（以后能查到这版训练集是怎么做出来的）
        values = asdict(self)
        values.pop("training_id")
        values.update(test_areas=list(self.test_areas), weights=WEIGHTS,
                      fit_on="train+val", street_to_aerial="quantile", max_year_gap=None,
                      street_only=STREET_ONLY, unused=UNUSED, exclusions="rules 1-3 + manual flags")
        return values


# 第 1 步读进来的 8 样东西装在一起，后面各步按名字取用。来源都在 bucket 的 datasets/<dataset_id>/ 下：
@dataclass
class PreparationInputs:
    cells: pd.DataFrame          # cells/（collector）：每个格子一行，位置、城市/郊区、航拍图路径、tile_ok、naip_year
    osm: pd.DataFrame            # osm_features/（collector）：每个格子 23 个地图特征
    streetview: pd.DataFrame     # streetview/（collector）：每张街景照片属于哪个格子、拍摄时间
    aerial_cell: pd.DataFrame    # labels/<aerial_id>/cell_labels/（labeler）：每个格子的航拍分，0–1
    aerial_tile: pd.DataFrame    # labels/<aerial_id>/tile_labels/（labeler）：每张航拍图的原始分，1–5
    street_cell: pd.DataFrame    # labels/<street_id>/cell_labels/（labeler）：每个格子的街景平均分 0–1，加照片数
    street_image: pd.DataFrame   # labels/<street_id>/image_labels/（labeler）：每张街景照片的原始分、说明、能不能用
    dimensions: list[str]        # labels/<street_id>/meta.json 里的 16 个维度名


# 第 1 步：读入所有表，并检查数据集完整、各表对得上（有问题直接报错停下，不带着坏数据往下跑）
def load_preparation_inputs(source: Inputs, config: PreparationConfig) -> PreparationInputs:
    
    # 检查采集完成：manifest.json（collector 写的）里 status 要是 "29/29 bboxes complete" 这样前后相等
    manifest = source.json("manifest.json")
    match = re.match(r"(\d+)/(\d+)", manifest["status"])
    if not match or match[1] != match[2]:
        raise ValueError(f"Dataset is incomplete: {manifest['status']}")

    # 检查维度名：两套标签的 meta.json（labeler 写的）必须一样，而且正好是 15 个目标 + hilly_terrain
    street, aerial = f"labels/{config.street_id}", f"labels/{config.aerial_id}"
    dimensions = source.metadata(street)["dimensions"]
    if dimensions != source.metadata(aerial)["dimensions"] or set(TARGETS + UNUSED) != set(dimensions):
        raise ValueError("Street/aerial dimensions do not match the expected targets.")

    # 读7张表（每张都是 29 个区域文件被areas拼起来的，见 51-65行Inputs.areas）；osm 去掉和 cells 重复的两列
    data = PreparationInputs(
        source.areas("cells"), source.areas("osm_features").drop(columns=["h3_index", "area_id"]),
        source.areas("streetview"), source.areas(f"{aerial}/cell_labels"),
        source.areas(f"{aerial}/tile_labels"), source.areas(f"{street}/cell_labels"),
        source.areas(f"{street}/image_labels"), dimensions,
    )

    # 航拍图不能用的格子（tile_ok 是 collector 标的，例如大部分是空白）labeler 不打分，这里直接去掉
    # 1026 已经清理过，一个都没有；新采集的数据集可能有几个
    bad = ~data.cells.tile_ok.astype(bool)
    if bad.any():
        LOG.warning("Dropping %s cells without a usable aerial tile", int(bad.sum()))
        keep = set(data.cells.cell_id[~bad])
        data.cells, data.osm = data.cells[~bad].reset_index(drop=True), data.osm[data.osm.cell_id.isin(keep)]
        data.streetview = data.streetview[data.streetview.cell_id.isin(keep)]
        data.street_cell = data.street_cell[data.street_cell.cell_id.isin(keep)]

    # 检查格子：编号、位置都不重复
    cells = data.cells
    if not cells.cell_id.is_unique or not cells.h3_index.is_unique:
        raise ValueError("Expected unique cells/H3 locations.")

    # 检查覆盖：地图特征、航拍分必须和格子一一对应（一个不多、一个不少）
    cell_ids = set(cells.cell_id)
    for table in [data.osm, data.aerial_cell, data.aerial_tile]:
        if not table.cell_id.is_unique or set(table.cell_id) != cell_ids:
            raise ValueError("Map/aerial tables do not cover exactly the source cells.")

    # 街景分只有部分格子有（有街景照片的才有），所以只要求"不重复、不出现陌生格子"
    if not data.street_cell.cell_id.is_unique or not set(data.street_cell.cell_id) <= cell_ids:
        raise ValueError("Street cell IDs are inconsistent.")
    return data


# 第 2 步：在删格子之前先分好 Train/Val/Test
# Test = 指定地点整个留出（指定四个区域整个给Test）；
# Val不按地方：剩下 25 个地方里，把每个地方都切成"小街区"（H3 第 8 级六边形，约 0.7 km²），随机抽 15% 的小街区给 Val。那为什么是"小街区"而不是一个一个格子随机抽？ 因为相邻格子几乎一样：同一条街、同一片树。
# Train 其余的小街区

# 输入 cells（第 1 步读的），输出同一张表多 4 列：region、block_id、split、fold
def assign_splits(cells: pd.DataFrame, config: PreparationConfig) -> pd.DataFrame:
    cells = cells.copy() #复制一份再加列，不改第 1 步读进来的原表

    # 每个区域算一个经纬度边框（最南、最北、最西、最东）和平均纬度
    boxes = cells.groupby("area_id").agg(
        lat0=("lat", "min"), lat1=("lat", "max"), lon0=("lon", "min"),
        lon1=("lon", "max"), lat=("lat", "mean"))

    # 把相邻的区域并成一组（"并查集"）：parent 记每个区域挂在谁下面，root 顺着往上找到这一组的组长
    parent = {area: area for area in boxes.index}

    def root(area):
        while parent[area] != area:
            parent[area] = parent[parent[area]]
            area = parent[area]
        return area

    # 两两比较区域边框之间的空隙（1 度纬度 ≈ 111 km，经度要乘 cos(纬度)），< 3 km 就并到同一组。避免出现 Test 考 sfrichmond，Train 里却有紧挨着的 sanfrancisco 的情况。
    for p, q in itertools.combinations(boxes.index, 2):
        dlat = max(0, boxes.lat0[q] - boxes.lat1[p], boxes.lat0[p] - boxes.lat1[q])
        dlon = max(0, boxes.lon0[q] - boxes.lon1[p], boxes.lon0[p] - boxes.lon1[q])
        if np.hypot(dlat * 111, dlon * 111 * np.cos(np.radians(boxes.lat[p]))) < config.adjacent_km:
            parent[root(p)] = root(q)

    # 每组用格子最多的那个区域名当地点名（例如 city_ca_sfrichmond_big 并进 city_ca_sanfrancisco_big）
    sizes = cells.groupby("area_id").size()
    regions = {area: max((other for other in boxes.index if root(other) == root(area)),
                         key=lambda other: sizes[other]) for area in boxes.index}

    # Test 地点：一个都不在这个数据集里（例如只采了一个小区域的试跑数据集）→ 不分 Test，只有 Train / Val；
    # 只有一部分在 → 多半是名字写错了，报错
    present = set(config.test_areas) & set(boxes.index)
    if not present:
        LOG.warning("None of the Test areas are in this dataset: no Test split")
    elif present != set(config.test_areas):
        raise ValueError(f"Unknown Test areas: {sorted(set(config.test_areas) - present)}")

    # 加 region 列；Test 地点所在的整组都算 Test
    cells["region"] = cells.area_id.map(regions)
    test_regions = {regions[area] for area in present}

    # 加 block_id 列：每个小格子所在的 H3 第 8 级大六边形
    cells["block_id"] = [h3.cell_to_parent(index, config.block_res) for index in cells.h3_index]

    # 每个大六边形算一个 0–1 的固定"随机数"（种子 + 编号的哈希，每次都一样），< 0.15 → Val，其余 → Train
    block_hash = cells.block_id.map(
        lambda block: int(hashlib.sha256(f"{config.seed}:{block}".encode()).hexdigest()[:8], 16) / 16**8)
    cells["split"] = np.where(cells.region.isin(test_regions), "test",
                              np.where(block_hash < config.val_share, "val", "train"))

    # 加 fold 列：另一个哈希除以 5 取余数 → 0–4 折；Test 不参与交叉验证，留空
    cells["fold"] = cells.block_id.map(
        lambda block: int(hashlib.sha256(f"{config.seed}:fold:{block}".encode()).hexdigest()[:8], 16)
        % config.n_folds).astype("Int64")
    cells.loc[cells.split.eq("test"), "fold"] = pd.NA
    return cells


# 第 3 步（读第 2 轮名单）：人工审核标记，一份冻结的 CSV（默认 {数据集}/corrections/v2/manual_flags.csv，目前只有 1026 有）
# 找不到就当作 0 条（新城市没人审核过，照常往下跑）；--manual-flags-csv none 可以主动关掉
# 返回：标记表（每行：审核员、格子、航拍还是街景、哪一项）+ CSV 的哈希（记进 meta.json）
def load_manual_flags(source: Inputs, csv_path: str | None):
    
    # 没指定路径就用默认位置
    columns = ["reviewer", "cell_id", "source", "dimension"]
    csv_path = csv_path or f"{source.root}/corrections/v2/manual_flags.csv"

    # 文件在不在：none = 主动不用；gs:// 去 bucket 查；否则查本地
    exists = (csv_path.lower() != "none" and
              (gcsfs.GCSFileSystem(token="google_default").exists(csv_path) if csv_path.startswith("gs://")
               else Path(csv_path).is_file()))

    # 不在：返回一张只有表头的空表，哈希为空
    if not exists:
        LOG.info("No manual reviewer flags (%s)", csv_path)
        return pd.DataFrame(columns=columns), None

    # 在：读进来（read_flags 见第 81 行），去掉完全重复的行，算哈希
    flags, raw_csv = read_flags(csv_path)
    LOG.info("Manual reviewer flags: %s", csv_path)
    return flags[columns].drop_duplicates(), hashlib.sha256(raw_csv).hexdigest()[:16]


# 第 3 步（汇总两轮名单）：汇总要删的格子，每条规则 / 每位审核员一行记录；被任何一条命中的格子整个删掉
# 输入：cells（第 2 步）、candidates（detect产出的可疑清单）、flags（人工审核）；输出：删除记录表，最后写成 exclusions.parquet
def collect_exclusions(cells: pd.DataFrame, candidates: pd.DataFrame, flags: pd.DataFrame) -> pd.DataFrame:
    # 检查：可疑清单和人工标记里的格子都必须是这个数据集里的
    known = set(cells.cell_id)
    if not set(candidates.cell_id) <= known or not set(flags.cell_id) <= known:
        raise ValueError("Candidate/manual flags contain unknown cell IDs.")
    records = []

    # 小工具：一条规则（或一位审核员）命中的格子去重排序，记成一小张表（格子、第几轮、规则名、细节）
    def add(ids, round_number, rule, detail=""):
        records.append(pd.DataFrame({"cell_id": sorted(set(ids)), "round": round_number,
                                     "rule": rule, "detail": detail}))

    # 第 1 轮：三条检测规则现场找出的格子，按规则分开记（rule:street_scene_keyword 等）
    for kind, group in candidates.groupby("candidate_type"):
        add(group.cell_id, 1, f"rule:{kind}")

    # 第 2 轮：人工审核，按审核员分开记（manual:wxz 等）；细节写他标了哪些"航拍/街景 + 维度"，最多 200 字
    for reviewer, group in flags.groupby("reviewer"):
        detail = "; ".join(sorted({f"{row.source}/{row.dimension}" for row in group.itertuples()}))[:200]
        add(group.cell_id, 2, f"manual:{reviewer}", detail)

    # 一个都没命中：返回只有表头的空表
    if not records:
        return pd.DataFrame(columns=["cell_id", "round", "rule", "detail", "area_id"])

    # 所有小表上下拼成一张，并补上每个格子所在的区域
    return pd.concat(records, ignore_index=True).merge(cells[["cell_id", "area_id"]], on="cell_id")


# 被第4步调用的计算工具，真正做"街景分换算到航拍尺度"的计算
def quantile_map(street_fit, aerial_fit, street_new):
    # 一个街景分在所有街景分里排在前百分之几，就换成航拍分里同样百分之几位置上的分。
    # street_fit 和 aerial_fit：同一批格子（Train + Val 里街景和航拍都有分的格子）的两种分
    # street_new 所有有街景分的格子，包括 Test

    street_fit = np.sort(street_fit[~np.isnan(street_fit)]) #用来学的街景分，先去掉空值，再从小到大排好。
    output = np.full(len(street_new), np.nan) #先准备一张空白答卷。 长度和要换算的街景分一样，每个位置先填空值。
    valid = ~np.isnan(street_new) # 标出哪些位置真的有分、需要换算。

    # 每个要换算的街景分，找它在排好的街景分里最前能插在第几位(low)、最后能插在第几位(high)
    low = np.searchsorted(street_fit, street_new[valid], side="left")
    high = np.searchsorted(street_fit, street_new[valid], side="right")

    # 取中间名次，换成百分比, 取航拍分布里同一百分比位置上的分；没有街景分的保持空值
    output[valid] = np.quantile(aerial_fit, np.clip((low + high) / 2 / len(street_fit), 0, 1))
    return output


# 第4步的的输出格式
@dataclass
class FusedScores:
    aerial: pd.DataFrame       # 航拍原始分（1–5），没换算
    street: pd.DataFrame       # 街景原始分（1–5），没换算
    targets: pd.DataFrame      # 15 个目标：换算 + 融合后的分（0–1）
    parameters: pd.DataFrame   # 每个维度两边的均值、标准差（只记录进 meta.json，不参与计算）
    curves: dict               # 每个维度的换算曲线（101 个分位点，记录进 meta.json）


# 第 4 步：街景校准到航拍尺度（只用 Train + Val 学曲线，Test 不参与学，但同样被换算），再按 WEIGHTS 融合
# 输入：data（第 1 步）、kept（删完格子后剩下的，带 split 列）；输出：FusedScores
def fuse_scores(data: PreparationInputs, kept: pd.DataFrame) -> FusedScores:

    # 两边格子分从 0–1 换回原始 1–5 分（×4+1），只留没被删的格子；缺的分保持空值，绝不当成 0
    aerial = data.aerial_cell.set_index("cell_id")[data.dimensions].reindex(kept.cell_id) * 4 + 1
    street = data.street_cell.set_index("cell_id")[data.dimensions].reindex(kept.cell_id) * 4 + 1

    # fit = 哪些格子能用来学曲线（Train + Val）
    fit = kept.split.isin(["train", "val"]).to_numpy()
    converted = pd.DataFrame(index=aerial.index, columns=list(WEIGHTS), dtype=float) #放换算后的街景分
    parameters, curves = [], {} #放每个维度的均值/标准差、换算曲线，最后写进 meta.json

    # 逐个维度（14 个）：用"Train + Val 里两边都有分"的格子学曲线，再把所有格子的街景分换算到航拍尺度
    for dimension in WEIGHTS:
        observed = (aerial[dimension].notna() & street[dimension].notna()).to_numpy() & fit
        a, s = aerial[dimension][observed], street[dimension][observed]
        if a.empty:
            raise ValueError(f"No paired calibration labels for {dimension}.")
        mu_a, sd_a, mu_s, sd_s = a.mean(), a.std(), s.mean(), s.std()
        parameters.append((dimension, int(observed.sum()), mu_a, sd_a, mu_s, sd_s))
        converted[dimension] = quantile_map(s.to_numpy(), a.to_numpy(), street[dimension].to_numpy())
        # 记下曲线：两边各取 101 个分位点（0%、1%…100%），写进 meta.json 方便画图检查
        grid = np.linspace(0, 1, 101)
        curves[dimension] = {"street_q": np.quantile(s, grid).round(4).tolist(),
                             "aerial_q": np.quantile(a, grid).round(4).tolist()}

    # 融合：两边都有 → 权重 × 换算后的街景 + (1 − 权重) × 航拍；只有一边 → 直接用那一边；都没有 → 空
    fused = pd.DataFrame(index=aerial.index, columns=TARGETS, dtype=float)
    for dimension, weight in WEIGHTS.items():
        a, s = aerial[dimension], converted[dimension]
        fused[dimension] = np.select([a.notna() & s.notna(), s.notna() & a.isna(), a.notna() & s.isna()],
                                     [weight * s + (1 - weight) * a, s, a], np.nan)

    # visual_clutter：只用街景原始分，不换算
    for dimension in STREET_ONLY:
        fused[dimension] = street[dimension]

    # 打包返回；目标从 1–5 换回 0–1（−1 再 ÷4）
    parameters = pd.DataFrame(parameters, columns=["dimension", "n_cells", "muA", "sdA", "muS", "sdS"]).set_index("dimension")
    return FusedScores(aerial, street, ((fused - 1) / 4).astype("float32"), parameters, curves)


# 第 5 步：拼成训练表（87 列）：身份信息、分组、航拍图路径、23 个地图特征、15 个目标、两边各自的分、质量信息
# 输入：data（第 1 步）、kept（第 3 步删完剩下的）、scores（第 4 步）；输出：训练表、列分组、每步剩多少格子
def assemble_table(data: PreparationInputs, kept: pd.DataFrame,
                   scores: FusedScores, config: PreparationConfig):
    
    # 身份和分组列：从kept取（cells 原有的列 + 第 2 步加的 region、split、block_id、fold）；加上数据集名、city（= 区域名）
    identifiers = ["cell_id", "h3_index", "area_id", "region", "area_type", "area_subtype",
                   "lat", "lon", "split", "block_id", "fold", "tile_path", "naip_year"]
    out = kept[identifiers].copy() #从这一刻起，out 就只有kept（也就是三条规则过滤完的）的 25,380 行。后面每次往out上增东西时，都是从 out留存的格子去取
    out.insert(1, "dataset_id", config.dataset_id)
    out.insert(3, "city", out.area_id) #city 就是区域名，训练时按城市统计用

    # 地图特征：按格子并进 23 个 osm 特征
    out = out.merge(data.osm, on="cell_id", how="left", validate="1:1")

    # 分数列：每个目标三列 —— 融合后的标准答案（训练用）、aerial__ 航拍原始分、street__ 街景原始分（后两个只是参考，0–1）
    for dimension in TARGETS:
        out[dimension] = scores.targets[dimension].to_numpy()
        out[f"aerial__{dimension}"] = (((scores.aerial[dimension] - 1) / 4).astype("float32").to_numpy()
                                      if dimension not in STREET_ONLY else np.float32("nan"))
        out[f"street__{dimension}"] = ((scores.street[dimension] - 1) / 4).astype("float32").to_numpy()

    # 质量列：每个格子街景最早 / 最晚拍摄月份（来自 streetview 的 captured_at），照片数 / 能用的照片数（来自街景 cell_labels）
    capture = data.streetview.assign(t=pd.to_datetime(data.streetview.captured_at, utc=True)).groupby("cell_id").t.agg(["min", "max"])
    dates = capture.assign(capture_min=capture["min"].dt.strftime("%Y-%m"),
                           capture_max=capture["max"].dt.strftime("%Y-%m"))[["capture_min", "capture_max"]]
    quality = data.street_cell.set_index("cell_id")[["n_images", "n_usable"]].join(dates)
    out = out.merge(quality, left_on="cell_id", right_index=True, how="left")

    # year_gap：航拍年份和街景拍摄年份（取最早、最晚的中间）差几年；只记录，不筛选
    mid_year = (pd.to_datetime(out.capture_min, format="%Y-%m").dt.year
                + pd.to_datetime(out.capture_max, format="%Y-%m").dt.year) / 2
    out["year_gap"] = (out.naip_year.astype(float) - mid_year).abs().astype("float32")

    # n_targets：这个格子有几个目标分；一个都没有的格子去掉（没有标准答案没法训练）；funnel 记下每步剩多少格子，目前没有格子会因为"一个目标都没有"被删
    out["n_targets"] = out[TARGETS].notna().sum(axis=1).astype("int8")
    funnel = [("cells in the dataset", len(data.cells)), ("after round 1 and round 2", len(out))]
    out = out[out.n_targets > 0]
    funnel.append(("has at least one target", len(out)))
    out = out.sort_values(["split", "cell_id"]).reset_index(drop=True)

    # 列的分组和顺序：训练表的列按组排好，打开看时一目了然（这份分组也写进 meta.json，训练时按组取列）
    features = [column for column in data.osm if column != "cell_id"]
    groups = {
        "id": ["cell_id", "dataset_id", "h3_index", "city", "region", "area_type", "area_subtype", "lat", "lon"],
        "split": ["split", "block_id", "fold"], "image": ["tile_path", "naip_year", "year_gap"],
        "tabular": features, "targets": TARGETS,
        "aerial_only": [f"aerial__{d}" for d in TARGETS if d not in STREET_ONLY],
        "street_only": [f"street__{d}" for d in TARGETS],
        "quality": ["n_images", "n_usable", "capture_min", "capture_max", "n_targets"],
    }
    out = out[[column for group in groups.values() for column in group]]

    # 数字特征统一成 float32（省一半空间，训练够用）
    for column in features + ["year_gap"]:
        out[column] = out[column].astype("float32")
    return out, groups, dict(funnel)


# 第 6 步（检查）：所有目标分必须在 0–1 之间
def validate_table(out: pd.DataFrame):
    values = out[TARGETS].to_numpy()
    if not 0 <= np.nanmin(values) <= np.nanmax(values) <= 1:
        raise ValueError("Targets must be normalized to 0-1.")


# 第 6 步（说明书）：meta.json 的内容 —— 这版训练集怎么做的、有多少格子、删了什么、换算曲线
def build_metadata(out, groups, funnel, scores, exclusions, flags, csv_hash, config):
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {
        # 名字、来源数据集、用的哪两套标签、设置记录（PreparationConfig.notebook_config）、创建时间
        "training_id": config.training_id, "dataset_ids": [config.dataset_id],
        "label_id": f"{config.street_id}+{config.aerial_id}+fused",
        "config": config.notebook_config(), "embeddings": {}, "created_at": timestamp, "updated_at": timestamp,
        # 数量：每步剩多少格子、Train/Val/Test 各多少格子和大六边形、每个区域多少格子
        "funnel": funnel, "counts": {split: int(out.split.eq(split).sum()) for split in ["train", "val", "test"]},
        "blocks": {split: int(out.loc[out.split.eq(split)].block_id.nunique()) for split in ["train", "val", "test"]},
        "cells_per_city": {city: int(count) for city, count in out.city.value_counts().sort_index().items()},
        "columns": groups, "cities": sorted(out.city.unique().tolist()), "n_cells": len(out),
        # 人工审核：多少条、多少格子、每位审核员标了多少、CSV 的哈希
        "manual_flags": {"rows": len(flags), "cells": int(flags.cell_id.nunique()),
                         "cells_per_reviewer": flags.groupby("reviewer").cell_id.nunique().to_dict(),
                         "csv_sha256": csv_hash},
        # 换算：方法、曲线、两边的均值和标准差
        "street_to_aerial_method": "quantile", "street_to_aerial_curve": scores.curves,
        "street_to_aerial_parameters": scores.parameters.round(4).to_dict("index"),
        # 删除：每条规则 / 每位审核员删了几个格子
        "removed_by_rule": exclusions.groupby("rule").cell_id.nunique().to_dict(),
        "notes": "targets are fused street+aerial scores (0 to 1, NaN = not used for that dimension); street scores put on the aerial scale by "
                 + "quantile matching; hilly_terrain is not used",
    }


# 完整准备：第 1–6 步按顺序跑，返回（训练表、删除记录、meta.json 内容）交给 write_prepared；main 用法 1 调用
def prepare_dataset(source: Inputs, config: PreparationConfig, manual_flags_csv: str | None = None):
    # 第 1–2 步：读入并检查，分组
    data = load_preparation_inputs(source, config)
    cells = assign_splits(data.cells, config)

    # 第 3 步：第 1 轮三条检测规则现场跑一遍（和"只检测"用法同一套代码），第 2 轮人工审核，汇总后删掉命中的格子
    spatial = SpatialConfig()
    candidates = detect(*load_inputs(source.root, config.street_id, config.aerial_id, spatial), spatial)
    candidates = candidates[candidates.cell_id.isin(cells.cell_id)]   # 第 1 步已去掉的格子（航拍图不能用）不用再删
    flags, csv_hash = load_manual_flags(source, manual_flags_csv)
    exclusions = collect_exclusions(cells, candidates, flags)
    removed = set(exclusions.cell_id) #要删的格子编号
    kept = cells.loc[~cells.cell_id.isin(removed)].reset_index(drop=True) #删完剩下的格子（带分组列），后面都用它

    # 第 4–6 步：换算 + 融合，拼表，检查，写说明书
    scores = fuse_scores(data, kept)
    out, groups, funnel = assemble_table(data, kept, scores, config)
    validate_table(out)
    metadata = build_metadata(out, groups, funnel, scores, exclusions, flags, csv_hash, config)
    LOG.info("Prepared %s cells / %s columns; splits: %s; removed %s cells",
             len(out), len(out.columns), metadata["counts"], len(removed))
    return out, exclusions, metadata


# 第 7 步：只写进一个新文件夹，已有的训练集永远不会被覆盖
# 默认位置 gs://<bucket>/training/<training-id>/（main 拼的），写 3 个文件：dataset.parquet、exclusions.parquet、meta.json
def write_prepared(result, output_dir: str):
    out, exclusions, metadata = result #prepare_dataset 交出来的三样

    # 写到 bucket：不许写进 datasets/（原始数据不动）；文件夹已存在就报错；然后逐个写文件
    if output_dir.startswith("gs://"):
        if "datasets" in output_dir.removeprefix("gs://").split("/")[1:]:
            raise ValueError("Prepared output must be outside source datasets/.")
        fs = gcsfs.GCSFileSystem(token="google_default")
        if fs.exists(output_dir):
            raise FileExistsError(f"Output already exists: {output_dir}. Choose a new directory.")
        for name, table in [("dataset.parquet", out), ("exclusions.parquet", exclusions)]:
            with fs.open(f"{output_dir}/{name}", "wb") as stream:
                table.to_parquet(stream, index=False)
        with fs.open(f"{output_dir}/meta.json", "w") as stream:
            json.dump(metadata, stream, indent=2, ensure_ascii=False)

    # 写到本地（没有 bucket，或 --output-dir 给了本地路径）：新建文件夹（已存在就报错），写 3 个文件
    else:
        folder = Path(output_dir)
        folder.mkdir(parents=True, exist_ok=False)
        out.to_parquet(folder / "dataset.parquet", index=False)
        exclusions.to_parquet(folder / "exclusions.parquet", index=False)
        (folder / "meta.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    LOG.info("Saved prepared dataset, metadata and exclusions to %s", output_dir)


# ==================== 命令行 ====================

# 定义 9 个命令行参数（python cli.py --help 能看到）
def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description="GreenPath data processor: detect candidates, or --prepare the training dataset")
    # 做什么
    cli.add_argument("--prepare", action="store_true", help="做完整的训练数据准备；不加 = 只检测")
    # 读哪份数据（bucket 名默认来自 .env 的 GREENPATH_BUCKET，docker-shell.sh 用 --env-file 传进容器）
    cli.add_argument("--bucket", default=os.environ.get("GREENPATH_BUCKET"))
    cli.add_argument("--dataset-id", default="1026_Full_data")
    cli.add_argument("--street-id", default="street_v7_gemini-2.5-flash", help="街景标签文件夹名（换 prompt / 模型时改）")
    cli.add_argument("--aerial-id", default="aerial_a5_gemini-2.5-flash", help="航拍标签文件夹名（换 prompt / 模型时改）")
    cli.add_argument("--input-root", help="可选：改读本地的数据集快照（测试用）")
    # 写到哪
    cli.add_argument("--training-id", help="这版训练集的名字（默认 <dataset-id>_<时间>，保证不重名）")
    cli.add_argument("--output-dir", help="输出文件夹（默认 gs://<bucket>/training/<training-id>；只检测时 gs://<bucket>/candidates/<时间>）")
    cli.add_argument("--manual-flags-csv", help="人工审核 CSV（本地或 gs://）；默认 {数据集}/corrections/v2/manual_flags.csv，找不到就跳过；none = 不用")
    return cli


# 程序入口：读参数 → 定好读哪、写哪 → 走用法 1（--prepare）或用法 2（只检测）
def main() -> None:
    # 日志：打印 INFO 级别；不打印每个文件的读写细节，只留汇总
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("fsspec").setLevel(logging.WARNING)
    logging.getLogger("gcsfs").setLevel(logging.WARNING)

    # 读参数；bucket 和本地快照至少要有一个
    cli = parser()
    args = cli.parse_args()
    if not args.input_root and not args.bucket:
        cli.error("Set --bucket / GREENPATH_BUCKET, or supply --input-root.")

    # 读哪：本地快照，或 gs://<bucket>/datasets/<dataset-id>；这次运行的编号 = UTC 时间 + 8 位随机码
    root = args.input_root or f"gs://{args.bucket}/datasets/{args.dataset_id}"
    run = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    # 写哪：结果默认写进 bucket；没给 bucket（只用本地 --input-root 测试）时才写到本地 outputs/dev/
    out_root = f"gs://{args.bucket}" if args.bucket else f"outputs/dev/{run}"

    # 用法 1：完整的训练数据准备 → gs://<bucket>/training/<training-id>/
    if args.prepare:
        training_id = args.training_id or f"{args.dataset_id}_{run}"
        config = PreparationConfig(dataset_id=args.dataset_id, street_id=args.street_id,
                                   aerial_id=args.aerial_id, training_id=training_id)
        result = prepare_dataset(Inputs(root), config, args.manual_flags_csv)
        write_prepared(result, args.output_dir or f"{out_root}/training/{training_id}")
        return

    # 用法 2：只检测（规则 3 的阈值用 SpatialConfig 里的默认值）→ gs://<bucket>/candidates/<时间>/candidates.parquet
    output = f"{args.output_dir or f'{out_root}/candidates/{run}'}/candidates.parquet"
    config = SpatialConfig()
    loaded = load_inputs(root, args.street_id, args.aerial_id, config)
    candidates = detect(*loaded, config)
    # 写可疑清单；文件里同时记下这次运行的设置（放在 Parquet 的元数据里）
    write_candidates(candidates, output, {
        "run_id": run, "input_root": root, "street_id": args.street_id, "aerial_id": args.aerial_id,
        "spatial": asdict(config), "rule_version": "1.2.0", "purpose": "exclusion candidates only",
        "policy": "Union of three detection rules; no correction actions or score edits",
    })
    LOG.info("Candidate records by rule: %s", candidates.groupby("candidate_type").size().to_dict())
    LOG.info("Wrote %s records (%s distinct source/image IDs) to %s", len(candidates),
             len(candidates[["source", "image_id"]].drop_duplicates()), output)

if __name__ == "__main__":
    main()
