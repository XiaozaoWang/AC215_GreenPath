"""
GreenPath data processor: turns the data-labeler scores into the training table.
  python cli.py --prepare   full prep: Train/Val/Test split, drop suspicious cells (3 rules + manual flags),
                            calibrate street scores to the aerial scale, fuse 15 targets.
                            Writes dataset.parquet, exclusions.parquet and meta.json to a new folder.
  python cli.py             detection only: writes candidates.parquet, removes nothing.
Reads --bucket / GREENPATH_BUCKET (datasets/<dataset-id>) or a local --input-root. Never overwrites an existing output folder.
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


# ==================== 1. Functions for reading data ====================

class Inputs:
    """Dataset root: gs://... or a local folder, with cells/, labels/ etc. under it, one Parquet per area."""

    def __init__(self, root: str):#When running python cli.py --prepare, cli.py:946 first builds the dataset path. Here we keep the root path, and if it starts with gs://, set up a tool that can read cloud files and store it in self.fs
        self.root = root.rstrip("/")
        self.fs = gcsfs.GCSFileSystem(token="google_default") if root.startswith("gs://") else None

    def metadata(self, folder: str) -> dict: #shortcut for json: reads meta.json in a folder
        return self.json(f"{folder}/meta.json")

    # Join the Parquet files of all areas in one folder (cells/, streetview/, labels/aerial_a5_gemini-2.5-flash/cell_labels/ and so on) into one table, and add an area_id column (= file name)
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

    #read one JSON file
    def json(self, relative: str) -> dict:
        path = f"{self.root}/{relative}"
        if self.fs:
            with self.fs.open(path) as stream:
                return json.load(stream)
        return json.loads(Path(path).read_text(encoding="utf-8"))

    #list the matching files, sorted by name. areas uses it to find the 29 files
    def files(self, pattern: str) -> list:
        return sorted(self.fs.glob(f"{self.root}/{pattern}") if self.fs else Path(self.root).glob(pattern))


# Reads the manual review flags CSV, returns the table plus the raw file bytes for the hash (so later we can answer "which version of the review flags was this training set built from").
def read_flags(path: str) -> tuple[pd.DataFrame, bytes]:
    if str(path).startswith("gs://"):
        with gcsfs.GCSFileSystem(token="google_default").open(path, "rb") as stream:
            data = stream.read()
    else:
        data = Path(path).read_bytes()
    return pd.read_csv(io.BytesIO(data)), data


# ==================== Rule 1: was the street photo taken indoors / in a tunnel / under a bridge ====================
# Doesn't look at the image, only at the wording in the rationale Gemini wrote when scoring
'''
Here we build word lists that cover different kinds of words (they can be put together into a sentence)
phrases() turns a word list into a regex. Longer words go first so no stray s is left after NEGATED_OBSTRUCTION removes a match
EXCLUDE means the view in this photo is blocked, very suspicious; with --prepare the whole cell it is in gets removed
KEEP means there is evidence that a tunnel or bridge is mentioned, but the person is outside with an open view, so keep it
If nothing is mentioned, Keep by default

classify_street runs the same 4 steps for every rationale of every image:
remove negations with NEGATED_OBSTRUCTION (the removal only applies to EXCLUDE, KEEP looks at the original sentence)
check if there is KEEP evidence, note it down;
try the 9 EXCLUDE patterns in order: strong evidence matched, EXCLUDE right away; weak evidence matched, KEEP if there is KEEP evidence, otherwise EXCLUDE. Nothing matched: KEEP (trust the photo by default).
'''

def phrases(words: list[str]) -> str:
    """Turn a word list into a regex: longer phrases first (match "parking garages" before "parking garage"), a space matches any whitespace."""
    patterns = [r"\s+".join(re.escape(word) for word in phrase.split())
                for phrase in sorted(words, key=len, reverse=True)]
    return "(?:" + "|".join(patterns) + ")"


# Word lists: edit them like normal words, write plurals yourself. parking lot is left out on purpose; only mentioning a building doesn't count either
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
# Words for the extra tunnel check (whole-word match, no plurals)
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

# Turn the word lists into matchers; only the sentence structure parts need hand-written regex
#\s+: whitespace in between; \b: must be a full word boundary, so garage won't match half of garages
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

# Negations ("not obstructed", "not taken in a garage"): remove them first so they aren't counted as evidence
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

# Each entry is (reason, regex, can KEEP evidence override it)
# The first 6 are direct evidence (indoors, blocked by a structure), removed right away; the last 3 are uncertain, kept if there is clear KEEP evidence
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


# One rationale in, ("EXCLUDE" / "KEEP", reason) out
def classify_street(rationale: str | None) -> tuple[str, str]:
    text = "" if pd.isna(rationale) else re.sub(r"\s+", " ", str(rationale)).strip()
    # Only remove the negated part, positive evidence elsewhere stays
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
    """Rule 1: the sentence check above says EXCLUDE, or the extra tunnel check matches (Gemini says usable, but the rationale has tunnel words + enclosure words)."""
    #Check A: pass each rationale to classify_street, it's a hit if the result is EXCLUDE.
    context = pd.DataFrame(
        images.rationale.map(classify_street).tolist(),
        index=images.index, columns=["decision", "reason"],
    )
    text = images.rationale.fillna("")
    
    #Check B: suspicious only if all conditions hold: usable=true, the rationale has tunnel and enclosure words, and no tree tunnel wording like tunnel-like, tunnel of, tunnel effect
    tunnel_places = r"\b(?:" + "|".join(map(re.escape, TUNNEL_WORDS)) + r")\b"
    tunnel = (images.usable.astype(bool)
              & text.str.contains(tunnel_places, case=False)
              & text.str.contains("|".join(map(re.escape, ENCLOSURE_WORDS)), case=False)
              & ~text.str.contains("|".join(map(re.escape, TREE_TUNNEL_PHRASES)), case=False))
    excluded = context.decision.eq("EXCLUDE")
    selected = excluded | tunnel
    result = images.loc[selected].copy()

    # Union of check A (classify_street says EXCLUDE) and check B (selected = excluded | tunnel), then write the reason and add flag columns
    # Each photo shows up only once; if both match, both reasons are written
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


# ==================== Rule 2: aerial tile is broken / misread ====================

def aerial_candidates(tiles: pd.DataFrame, dimensions: list[str]) -> pd.DataFrame:
    """Rule 2: every dimension has the lowest score, or the "misread" condition (at least 14 scores of 1 but open_landscape is 5). Two checks per tile, selected if either one hits"""
    scores = tiles[dimensions]
    minimum = (scores.eq(1) | scores.eq(0) | scores.isna()).all(axis=1) & scores.eq(1).any(axis=1) #every score is 1, 0 or empty, and at least one is a real 1
    misread = scores.eq(1).sum(axis=1).ge(14) & tiles.open_landscape.eq(5)
    result = tiles.loc[minimum | misread].copy()#tile has 25,896 rows, here we only pick the hit rows and copy them into a small table in memory
    #add 4 new columns to it.
    result["candidate_type"] = np.where(minimum.loc[result.index], "aerial_all_minimum", "misread_aerial")
    result["reason"] = np.where(
        minimum.loc[result.index],
        "Every available raw aerial score is 1; at least one is available.",
        "At least 14 raw scores are 1 and open_landscape is 5 (misread aerial check).",
    )
    result["source"] = "aerial"
    result["score_scale"] = "1-5 (0 = unavailable)"
    return result


# ==================== Rule 3: aerial score too far from its neighbors ====================

@dataclass(frozen=True)
class SpatialConfig:
    dimensions: tuple[str, ...] = ("farmland_pastoral", "grassland")
    low_score: float = 0.25          # own score <= 2 counts as "low"
    high_neighbor: float = 0.75      # neighbor median >= 4 counts as "high"
    min_difference: float = 0.75     # neighbors at least 3 points higher (lowest score is 1, so a gap of 0.75 = 3 points)
    min_neighbors: int = 4           # at least 4 of the 6 neighbors have a score, too few means no call
    agreement: float = 0.75          # at least 75% of neighbors within 1 point of the median, so the neighbors agree
    tolerance: float = 0.25          # at least 75% of neighbors within 1 point of the median, so the neighbors agree

SUBURB_ONLY = {"farmland_pastoral", "grassland"}
SPATIAL_COLUMNS = [
    "area_id", "cell_id", "h3_index", "tile_path", "dimension", "original_score",
    "neighbor_median", "score_difference", "n_neighbors", "neighbor_agreement",
    "candidate_type", "source", "score_scale", "reason",
] #Rule 3 builds its own small table with only the caught cells, SPATIAL_COLUMNS is the header of that table.


def spatial_candidates(cells: pd.DataFrame, config: SpatialConfig) -> pd.DataFrame:
    # One score per H3 location, neighbors across area borders count too; groupby.first keeps the notebook's "first non-null value in file order"
    scores = cells.groupby("h3_index", sort=False)[list(config.dimensions)].first().astype(float)
    records = []
    for row in cells.itertuples(index=False):
        ring = set(h3.grid_disk(row.h3_index, 1)) - {row.h3_index}
        neighbors = scores.reindex(sorted(ring)) #use h3.grid_disk(location, 1) to find the 6 direct neighbors and get their scores;
        for dimension in config.dimensions:
            if dimension in SUBURB_ONLY and row.area_type != "suburb": #skip if not suburb. No farmland or grassland in a city is normal, not an error;
                continue
            target = float(getattr(row, dimension))
            if not np.isfinite(target) or target > config.low_score: #skip if own score > 2, only check the "low" ones;
                continue
            values = neighbors[dimension].to_numpy(float)
            values = values[np.isfinite(values)]  # 0 is a valid score
            if len(values) < config.min_neighbors: #skip if fewer than 4 neighbors have a score;
                continue
            median = float(np.median(values))
            difference = median - target  # signed: only catch "self low, neighbors high"
            agreement = float(np.mean(np.abs(values - median) <= config.tolerance)) #neighbor median, gap, and share of neighbors that agree.
            if (median >= config.high_neighbor and difference >= config.min_difference
                    and agreement >= config.agreement):
                #record one row if all three hold: median >= 4, gap >= 3, neighbors agree.
                records.append({
                    "area_id": row.area_id, "cell_id": row.cell_id, "h3_index": row.h3_index,
                    "tile_path": row.tile_path, "dimension": dimension, "original_score": target,
                    "neighbor_median": median, "score_difference": difference,
                    "n_neighbors": len(values), "neighbor_agreement": agreement,
                    "candidate_type": "spatial_outlier", "source": "aerial", "score_scale": "0-1",
                    "reason": "Low aerial score surrounded by consistently high-scoring H3 neighbors.",
                })
    return pd.DataFrame(records, columns=SPATIAL_COLUMNS)


# ==================== Detection only (without --prepare) ====================

#prepare the inputs
def load_inputs(root: str, street_id: str, aerial_id: str, spatial: SpatialConfig):
    # Decide where to read: the dataset root and the names of the two label folders
    # root defaults to gs://ac215-notebook/datasets/1026_Full_data
    source = Inputs(root)
    street_folder, aerial_folder = f"labels/{street_id}", f"labels/{aerial_id}"

    # Read the 16 dimensions of each label set. Dimension names aren't hardcoded, the labeler's record decides; fail right away if they don't match
    street_dims = source.metadata(street_folder)["dimensions"]
    aerial_dims = source.metadata(aerial_folder)["dimensions"]
    if not street_dims or not aerial_dims or set(spatial.dimensions) - set(aerial_dims):
        raise ValueError("Missing scoring dimensions or unsupported spatial dimensions.")

    # Read 5 tables: cells/ and streetview/ are written by the collector; image_labels/, tile_labels/, cell_labels/ by the labeler.
    # areas() joins the per-area files and adds an area_id column
    cells = source.areas("cells", ["cell_id", "h3_index", "tile_path", "area_type"])
    streetview = source.areas("streetview", ["image_id", "cell_id", "h3_index", "image_path"])
    street_raw = source.areas(f"{street_folder}/image_labels", ["image_id", "usable", "rationale", *street_dims])
    aerial_raw = source.areas(f"{aerial_folder}/tile_labels", ["cell_id", "rationale", *aerial_dims])
    aerial_normalized = source.areas(f"{aerial_folder}/cell_labels", ["cell_id", *spatial.dimensions])

    # Build 3 inputs
    street = streetview.merge(street_raw, on=["area_id", "image_id"], how="inner", validate="many_to_one") #input for rule 1: which cell the photo belongs to + Gemini's rationale for the photo
    keys = ["area_id", "cell_id"]
    tiles = aerial_raw.merge(cells.drop(columns="area_type"), on=keys, how="left", validate="one_to_one") #input for rule 2: raw aerial scores + location and image path, goes to rule 2
    aerial = aerial_normalized.merge(cells, on=keys, how="left", validate="one_to_one") #input for rule 3: aerial cell scores + location and city/suburb

    #completeness check: every aerial score must find its cell info; every photo must have a path. Then log and return for detect
    if tiles.tile_path.isna().any() or aerial.h3_index.isna().any() or aerial.area_type.isna().any():
        raise ValueError("Aerial labels do not resolve to complete cell metadata.")
    if street.image_path.isna().any():
        raise ValueError("Street image paths are missing.")
    LOG.info("Loaded %s street relationships and %s aerial cells across %s areas",
             len(street), len(aerial), cells.area_id.nunique())
    return street, tiles, aerial, street_dims, aerial_dims

# Run rules 1, 2, 3 and combine them into one list
def detect(street: pd.DataFrame, tiles: pd.DataFrame, cells: pd.DataFrame,
           street_dimensions: list[str], aerial_dimensions: list[str],
           config: SpatialConfig) -> pd.DataFrame:

    # Each of the three rules returns a small table with only the caught rows
    frames = [street_candidates(street), aerial_candidates(tiles, aerial_dimensions),
              spatial_candidates(cells, config)] #the aerial built by load_inputs is called cells here in detect.
    # Merge street and aerial dimension names, dedupe, keep the order
    raw_dimensions = list(dict.fromkeys([*street_dimensions, *aerial_dimensions])) 

    # Raw 1-5 scores get a raw__ prefix, to keep them apart from rule 3's 0-1 scores (original_score)
    for frame in frames:
        frame.rename(columns={d: f"raw__{d}" for d in raw_dimensions}, inplace=True)
        if len(frame) and frame.source.eq("aerial").all():
            frame["image_id"] = frame.cell_id
            frame["image_path"] = frame.tile_path

    # Common header, combine into one table, aligning the columns of the three small tables
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

    # Text columns become strings; number columns become nullable numbers; neighbor count is an integer
    result[text_columns] = result[text_columns].astype("string")
    for column in set(columns) - set(text_columns) - {"n_neighbors"}:
        result[column] = result[column].astype("Float64")
    result["n_neighbors"] = result.n_neighbors.astype("Int64")
    return result

#--prepare doesn't run this part
def write_candidates(candidates: pd.DataFrame, output: str, metadata: dict) -> None:
    table = pa.Table.from_pandas(candidates, preserve_index=False)
    table = table.replace_schema_metadata({
        **(table.schema.metadata or {}),
        b"greenpath.detection": json.dumps(metadata, sort_keys=True).encode(),
    })
    if output.startswith("gs://"):
        # Writing to the bucket needs an explicit path, and not under datasets/ (source data stays untouched)
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


# ==================== Training data prep (--prepare) ====================
'''
prepare_dataset runs 7 steps in order (all in memory, only step 7 writes files):
Step 1 load_preparation_inputs: read the 8 tables the collector / labeler wrote to the bucket, check they are complete and match
Step 2 assign_splits: split Train / Val / Test first (before removing cells, so removals don't affect the split)
Step 3 detect + load_manual_flags + collect_exclusions: round 1 the three rules, round 2 manual review, hit cells are removed entirely
Step 4 fuse_scores: convert street scores to the aerial scale (curves learned on Train + Val only), then fuse into 15 targets with WEIGHTS
Step 5 assemble_table: build the training table (one row per cell, 87 columns)
Step 6 validate_table + build_metadata: check targets are in 0-1, build the contents of meta.json
Step 7 write_prepared: write dataset.parquet / exclusions.parquet / meta.json to a new folder
'''

# Street weight in the fusion (the rest is aerial). Source: set from a blind review of 1000 cells; "how much" trusts aerial (tree 0.1), "how nice it looks" trusts street (architectural 0.7)
WEIGHTS = {
    "tree_canopy": 0.1, "forest_environment": 0.4, "grassland": 0.3,
    "open_landscape": 0.3, "road_width": 0.3, "water_access": 0.0,
    "urban_density": 0.1, "street_enclosure": 0.2, "farmland_pastoral": 0.2,
    "industrial_area": 0.5, "historic_character": 0.6, "commercial_district": 0.6,
    "scenic_appeal": 0.6, "architectural_character": 0.7,
}
STREET_ONLY = ["visual_clutter"]          # street only (clutter can't be seen from aerial)
UNUSED = ["hilly_terrain"]                # not used (slopes can't be seen from aerial)
TARGETS = list(WEIGHTS) + STREET_ONLY     # 15 targets = 14 fused + visual_clutter


# Settings for this training set version; main builds it from CLI args (dataset-id, street-id, aerial-id, training-id), the rest use the defaults here
@dataclass(frozen=True)
class PreparationConfig:
    dataset_id: str = "1026_Full_data"                 # which dataset under datasets/ in the bucket to read
    street_id: str = "street_v7_gemini-2.5-flash"      # street label folder labels/<street_id>/
    aerial_id: str = "aerial_a5_gemini-2.5-flash"      # aerial label folder labels/<aerial_id>/
    seed: int = 42                                     # hash seed for splitting: the same seed gives the exact same split every time
    # places held out entirely as Test (areas within 3 km of them are held out too)
    test_areas: tuple[str, ...] = (
        "city_ma_boston_big", "sub_co_palisade_farm",
        "city_ny_ithaca_mid", "city_ca_santacruz_mid",
    )
    adjacent_km: float = 3          # two areas whose boxes are < 3 km apart count as the same place
    block_res: int = 8              # split Train / Val by H3 level 8 hexagons (one hexagon is about 0.7 sq km and holds many small cells)
    val_share: float = 0.15         # about 15% of the hexagons go to Val
    n_folds: int = 5                # Train + Val are split again into 5 folds (for cross-validation)
    training_id: str = "full_v2"    # name of this training set (main passes it in, default <dataset-id>_<time>)

    def notebook_config(self):
        # Settings record written into meta.json: splits, weights, conversion method, removal rules (so later we can see how this training set was made)
        values = asdict(self)
        values.pop("training_id")
        values.update(test_areas=list(self.test_areas), weights=WEIGHTS,
                      fit_on="train+val", street_to_aerial="quantile", max_year_gap=None,
                      street_only=STREET_ONLY, unused=UNUSED, exclusions="rules 1-3 + manual flags")
        return values


# The 8 things read in step 1, kept together so later steps can take them by name. All come from datasets/<dataset_id>/ in the bucket:
@dataclass
class PreparationInputs:
    cells: pd.DataFrame          # cells/ (collector): one row per cell, location, city/suburb, aerial tile path, tile_ok, naip_year
    osm: pd.DataFrame            # osm_features/ (collector): 23 map features per cell
    streetview: pd.DataFrame     # streetview/ (collector): which cell each street photo belongs to, capture time
    aerial_cell: pd.DataFrame    # labels/<aerial_id>/cell_labels/ (labeler): aerial score per cell, 0-1
    aerial_tile: pd.DataFrame    # labels/<aerial_id>/tile_labels/ (labeler): raw score per aerial tile, 1-5
    street_cell: pd.DataFrame    # labels/<street_id>/cell_labels/ (labeler): mean street score per cell 0-1, plus photo counts
    street_image: pd.DataFrame   # labels/<street_id>/image_labels/ (labeler): raw scores, rationale and usable flag per street photo
    dimensions: list[str]        # the 16 dimension names in labels/<street_id>/meta.json


# Step 1: read all tables and check the dataset is complete and the tables match (stop with an error on any problem, don't carry bad data forward)
def load_preparation_inputs(source: Inputs, config: PreparationConfig) -> PreparationInputs:
    
    # Check collection is done: status in manifest.json (written by the collector) must have both numbers equal, like "29/29 bboxes complete"
    manifest = source.json("manifest.json")
    match = re.match(r"(\d+)/(\d+)", manifest["status"])
    if not match or match[1] != match[2]:
        raise ValueError(f"Dataset is incomplete: {manifest['status']}")

    # Check dimension names: meta.json of both label sets (written by the labeler) must match, and be exactly the 15 targets + hilly_terrain
    street, aerial = f"labels/{config.street_id}", f"labels/{config.aerial_id}"
    dimensions = source.metadata(street)["dimensions"]
    if dimensions != source.metadata(aerial)["dimensions"] or set(TARGETS + UNUSED) != set(dimensions):
        raise ValueError("Street/aerial dimensions do not match the expected targets.")

    # Read 7 tables (each is the 29 area files joined by areas, see Inputs.areas at lines 45-59); drop the two osm columns that duplicate cells
    data = PreparationInputs(
        source.areas("cells"), source.areas("osm_features").drop(columns=["h3_index", "area_id"]),
        source.areas("streetview"), source.areas(f"{aerial}/cell_labels"),
        source.areas(f"{aerial}/tile_labels"), source.areas(f"{street}/cell_labels"),
        source.areas(f"{street}/image_labels"), dimensions,
    )

    # Cells with an unusable aerial tile (tile_ok is set by the collector, e.g. mostly blank) aren't scored by the labeler, drop them here
    # 1026 was already cleaned, so there are none; a newly collected dataset may have a few
    bad = ~data.cells.tile_ok.astype(bool)
    if bad.any():
        LOG.warning("Dropping %s cells without a usable aerial tile", int(bad.sum()))
        keep = set(data.cells.cell_id[~bad])
        data.cells, data.osm = data.cells[~bad].reset_index(drop=True), data.osm[data.osm.cell_id.isin(keep)]
        data.streetview = data.streetview[data.streetview.cell_id.isin(keep)]
        data.street_cell = data.street_cell[data.street_cell.cell_id.isin(keep)]

    # Check cells: no duplicate IDs or locations
    cells = data.cells
    if not cells.cell_id.is_unique or not cells.h3_index.is_unique:
        raise ValueError("Expected unique cells/H3 locations.")

    # Check coverage: map features and aerial scores must match the cells one to one (none extra, none missing)
    cell_ids = set(cells.cell_id)
    for table in [data.osm, data.aerial_cell, data.aerial_tile]:
        if not table.cell_id.is_unique or set(table.cell_id) != cell_ids:
            raise ValueError("Map/aerial tables do not cover exactly the source cells.")

    # Only some cells have street scores (the ones with street photos), so we only require "no duplicates, no unknown cells"
    if not data.street_cell.cell_id.is_unique or not set(data.street_cell.cell_id) <= cell_ids:
        raise ValueError("Street cell IDs are inconsistent.")
    return data


# Step 2: split Train/Val/Test before removing any cells
# Test = the chosen places held out entirely (the four chosen areas all go to Test);
# Val is not by place: in the other 25 places, cut each place into "small blocks" (H3 level 8 hexagons, about 0.7 sq km) and randomly pick 15% of the blocks for Val. Why blocks and not random single cells? Because neighboring cells are almost the same: same street, same trees.
# Train = the rest of the blocks

# Input cells (read in step 1), output the same table with 4 more columns: region, block_id, split, fold
def assign_splits(cells: pd.DataFrame, config: PreparationConfig) -> pd.DataFrame:
    cells = cells.copy() #copy before adding columns, so the original table from step 1 isn't changed

    # For each area, get a lat/lon box (south, north, west, east edges) and the mean latitude
    boxes = cells.groupby("area_id").agg(
        lat0=("lat", "min"), lat1=("lat", "max"), lon0=("lon", "min"),
        lon1=("lon", "max"), lat=("lat", "mean"))

    # Merge neighboring areas into groups ("union-find"): parent records which area each one hangs under, root walks up to the group's leader
    parent = {area: area for area in boxes.index}

    def root(area):
        while parent[area] != area:
            parent[area] = parent[parent[area]]
            area = parent[area]
        return area

    # Compare the gap between the boxes of every pair of areas (1 degree of latitude is about 111 km, longitude is multiplied by cos(latitude)), same group if < 3 km. This avoids testing on sfrichmond while Train has sanfrancisco right next to it.
    for p, q in itertools.combinations(boxes.index, 2):
        dlat = max(0, boxes.lat0[q] - boxes.lat1[p], boxes.lat0[p] - boxes.lat1[q])
        dlon = max(0, boxes.lon0[q] - boxes.lon1[p], boxes.lon0[p] - boxes.lon1[q])
        if np.hypot(dlat * 111, dlon * 111 * np.cos(np.radians(boxes.lat[p]))) < config.adjacent_km:
            parent[root(p)] = root(q)

    # Each group is named after its area with the most cells (e.g. city_ca_sfrichmond_big is merged into city_ca_sanfrancisco_big)
    sizes = cells.groupby("area_id").size()
    regions = {area: max((other for other in boxes.index if root(other) == root(area)),
                         key=lambda other: sizes[other]) for area in boxes.index}

    # Test places: if none are in this dataset (e.g. a trial dataset with one small area), there is no Test split, only Train / Val;
    # if only some are there, a name is probably misspelled, so raise an error
    present = set(config.test_areas) & set(boxes.index)
    if not present:
        LOG.warning("None of the Test areas are in this dataset: no Test split")
    elif present != set(config.test_areas):
        raise ValueError(f"Unknown Test areas: {sorted(set(config.test_areas) - present)}")

    # Add the region column; the whole group of a Test place counts as Test
    cells["region"] = cells.area_id.map(regions)
    test_regions = {regions[area] for area in present}

    # Add the block_id column: the H3 level 8 hexagon each small cell is in
    cells["block_id"] = [h3.cell_to_parent(index, config.block_res) for index in cells.h3_index]

    # Each hexagon gets a fixed 0-1 "random number" (hash of seed + ID, same every time), < 0.15 goes to Val, the rest to Train
    block_hash = cells.block_id.map(
        lambda block: int(hashlib.sha256(f"{config.seed}:{block}".encode()).hexdigest()[:8], 16) / 16**8)
    cells["split"] = np.where(cells.region.isin(test_regions), "test",
                              np.where(block_hash < config.val_share, "val", "train"))

    # Add the fold column: another hash mod 5 gives folds 0-4; Test isn't part of cross-validation, left empty
    cells["fold"] = cells.block_id.map(
        lambda block: int(hashlib.sha256(f"{config.seed}:fold:{block}".encode()).hexdigest()[:8], 16)
        % config.n_folds).astype("Int64")
    cells.loc[cells.split.eq("test"), "fold"] = pd.NA
    return cells


# Step 3 (read the round 2 list): manual review flags, a frozen CSV (default {dataset}/corrections/v2/manual_flags.csv, only 1026 has one for now)
# If not found, treat it as 0 rows (nobody has reviewed a new city yet, so just keep going); --manual-flags-csv none turns it off
# Returns: flags table (each row: reviewer, cell, aerial or street, which dimension) + the CSV hash (recorded in meta.json)
def load_manual_flags(source: Inputs, csv_path: str | None):
    
    # Use the default location if no path is given
    columns = ["reviewer", "cell_id", "source", "dimension"]
    csv_path = csv_path or f"{source.root}/corrections/v2/manual_flags.csv"

    # Does the file exist: none = turned off; gs:// checks the bucket; otherwise check locally
    exists = (csv_path.lower() != "none" and
              (gcsfs.GCSFileSystem(token="google_default").exists(csv_path) if csv_path.startswith("gs://")
               else Path(csv_path).is_file()))

    # Not there: return an empty table with only the header, and no hash
    if not exists:
        LOG.info("No manual reviewer flags (%s)", csv_path)
        return pd.DataFrame(columns=columns), None

    # There: read it (read_flags is at line 75), drop exact duplicate rows, compute the hash
    flags, raw_csv = read_flags(csv_path)
    LOG.info("Manual reviewer flags: %s", csv_path)
    return flags[columns].drop_duplicates(), hashlib.sha256(raw_csv).hexdigest()[:16]


# Step 3 (combine both rounds): collect the cells to remove, one record per rule / per reviewer; a cell hit by any of them is removed entirely
# Input: cells (step 2), candidates (the suspicious list from detect), flags (manual review); output: the removal records, written as exclusions.parquet at the end
def collect_exclusions(cells: pd.DataFrame, candidates: pd.DataFrame, flags: pd.DataFrame) -> pd.DataFrame:
    # Check: cells in the candidate list and in the manual flags must all be in this dataset
    known = set(cells.cell_id)
    if not set(candidates.cell_id) <= known or not set(flags.cell_id) <= known:
        raise ValueError("Candidate/manual flags contain unknown cell IDs.")
    records = []

    # Small helper: dedupe and sort the cells hit by one rule (or one reviewer), record them as a small table (cell, round, rule name, detail)
    def add(ids, round_number, rule, detail=""):
        records.append(pd.DataFrame({"cell_id": sorted(set(ids)), "round": round_number,
                                     "rule": rule, "detail": detail}))

    # Round 1: cells found live by the three detection rules, recorded per rule (rule:street_scene_keyword etc.)
    for kind, group in candidates.groupby("candidate_type"):
        add(group.cell_id, 1, f"rule:{kind}")

    # Round 2: manual review, recorded per reviewer (manual:wxz etc.); detail lists which "aerial/street + dimension" they flagged, max 200 characters
    for reviewer, group in flags.groupby("reviewer"):
        detail = "; ".join(sorted({f"{row.source}/{row.dimension}" for row in group.itertuples()}))[:200]
        add(group.cell_id, 2, f"manual:{reviewer}", detail)

    # Nothing hit: return an empty table with only the header
    if not records:
        return pd.DataFrame(columns=["cell_id", "round", "rule", "detail", "area_id"])

    # Stack all the small tables into one and add the area of each cell
    return pd.concat(records, ignore_index=True).merge(cells[["cell_id", "area_id"]], on="cell_id")


# Helper called by step 4, does the actual "street score to aerial scale" math
def quantile_map(street_fit, aerial_fit, street_new):
    # Whatever percentile a street score is at among all street scores, it becomes the aerial score at the same percentile.
    # street_fit and aerial_fit: the two scores of the same cells (cells in Train + Val that have both street and aerial scores)
    # street_new: all cells with a street score, including Test

    street_fit = np.sort(street_fit[~np.isnan(street_fit)]) #street scores to learn from, drop empty values first, then sort from small to large.
    output = np.full(len(street_new), np.nan) #start with a blank answer sheet. Same length as the street scores to convert, every slot starts empty.
    valid = ~np.isnan(street_new) # mark which slots really have a score and need converting.

    # For each street score to convert, find the earliest (low) and latest (high) position it can be inserted at in the sorted street scores
    low = np.searchsorted(street_fit, street_new[valid], side="left")
    high = np.searchsorted(street_fit, street_new[valid], side="right")

    # Take the middle rank, turn it into a percentage, take the aerial score at the same percentage; cells without a street score stay empty
    output[valid] = np.quantile(aerial_fit, np.clip((low + high) / 2 / len(street_fit), 0, 1))
    return output


# Output format of step 4
@dataclass
class FusedScores:
    aerial: pd.DataFrame       # raw aerial scores (1-5), not converted
    street: pd.DataFrame       # raw street scores (1-5), not converted
    targets: pd.DataFrame      # 15 targets: scores after conversion + fusion (0-1)
    parameters: pd.DataFrame   # mean and std of both sides per dimension (only recorded in meta.json, not used in the math)
    curves: dict               # conversion curve per dimension (101 quantiles, recorded in meta.json)


# Step 4: calibrate street to the aerial scale (curves learned on Train + Val only, Test isn't used for learning but is converted the same way), then fuse with WEIGHTS
# Input: data (step 1), kept (cells left after removal, with the split column); output: FusedScores
def fuse_scores(data: PreparationInputs, kept: pd.DataFrame) -> FusedScores:

    # Convert both cell scores from 0-1 back to raw 1-5 (*4+1), only for cells not removed; missing scores stay empty, never treated as 0
    aerial = data.aerial_cell.set_index("cell_id")[data.dimensions].reindex(kept.cell_id) * 4 + 1
    street = data.street_cell.set_index("cell_id")[data.dimensions].reindex(kept.cell_id) * 4 + 1

    # fit = which cells can be used to learn the curves (Train + Val)
    fit = kept.split.isin(["train", "val"]).to_numpy()
    converted = pd.DataFrame(index=aerial.index, columns=list(WEIGHTS), dtype=float) #holds the converted street scores
    parameters, curves = [], {} #holds each dimension's mean/std and conversion curve, written to meta.json at the end

    # For each dimension (14): learn the curve on cells "with both scores in Train + Val", then convert every cell's street score to the aerial scale
    for dimension in WEIGHTS:
        observed = (aerial[dimension].notna() & street[dimension].notna()).to_numpy() & fit
        a, s = aerial[dimension][observed], street[dimension][observed]
        if a.empty:
            raise ValueError(f"No paired calibration labels for {dimension}.")
        mu_a, sd_a, mu_s, sd_s = a.mean(), a.std(), s.mean(), s.std()
        parameters.append((dimension, int(observed.sum()), mu_a, sd_a, mu_s, sd_s))
        converted[dimension] = quantile_map(s.to_numpy(), a.to_numpy(), street[dimension].to_numpy())
        # Save the curve: 101 quantiles from each side (0%, 1% ... 100%), written to meta.json so it's easy to plot and check
        grid = np.linspace(0, 1, 101)
        curves[dimension] = {"street_q": np.quantile(s, grid).round(4).tolist(),
                             "aerial_q": np.quantile(a, grid).round(4).tolist()}

    # Fuse: if both exist, weight * converted street + (1 - weight) * aerial; if only one side, use that side; if neither, empty
    fused = pd.DataFrame(index=aerial.index, columns=TARGETS, dtype=float)
    for dimension, weight in WEIGHTS.items():
        a, s = aerial[dimension], converted[dimension]
        fused[dimension] = np.select([a.notna() & s.notna(), s.notna() & a.isna(), a.notna() & s.isna()],
                                     [weight * s + (1 - weight) * a, s, a], np.nan)

    # visual_clutter: raw street score only, no conversion
    for dimension in STREET_ONLY:
        fused[dimension] = street[dimension]

    # Pack and return; targets go from 1-5 back to 0-1 (minus 1, then divide by 4)
    parameters = pd.DataFrame(parameters, columns=["dimension", "n_cells", "muA", "sdA", "muS", "sdS"]).set_index("dimension")
    return FusedScores(aerial, street, ((fused - 1) / 4).astype("float32"), parameters, curves)


# Step 5: build the training table (87 columns): IDs, splits, aerial tile path, 23 map features, 15 targets, each side's own scores, quality info
# Input: data (step 1), kept (what's left after step 3), scores (step 4); output: training table, column groups, cells left after each step
def assemble_table(data: PreparationInputs, kept: pd.DataFrame,
                   scores: FusedScores, config: PreparationConfig):
    
    # ID and split columns: taken from kept (the original cells columns + region, split, block_id, fold added in step 2); plus the dataset name and city (= area name)
    identifiers = ["cell_id", "h3_index", "area_id", "region", "area_type", "area_subtype",
                   "lat", "lon", "split", "block_id", "fold", "tile_path", "naip_year"]
    out = kept[identifiers].copy() #from here on, out only has the 25,380 rows of kept (what's left after the three rules). Everything added to out later is taken for the cells kept in out
    out.insert(1, "dataset_id", config.dataset_id)
    out.insert(3, "city", out.area_id) #city is just the area name, used for per-city stats in training

    # Map features: join the 23 osm features by cell
    out = out.merge(data.osm, on="cell_id", how="left", validate="1:1")

    # Score columns: three per target, the fused ground truth (used for training), aerial__ raw aerial score, street__ raw street score (the last two are only for reference, 0-1)
    for dimension in TARGETS:
        out[dimension] = scores.targets[dimension].to_numpy()
        out[f"aerial__{dimension}"] = (((scores.aerial[dimension] - 1) / 4).astype("float32").to_numpy()
                                      if dimension not in STREET_ONLY else np.float32("nan"))
        out[f"street__{dimension}"] = ((scores.street[dimension] - 1) / 4).astype("float32").to_numpy()

    # Quality columns: earliest / latest street capture month per cell (from captured_at in streetview), photo count / usable photo count (from street cell_labels)
    capture = data.streetview.assign(t=pd.to_datetime(data.streetview.captured_at, utc=True)).groupby("cell_id").t.agg(["min", "max"])
    dates = capture.assign(capture_min=capture["min"].dt.strftime("%Y-%m"),
                           capture_max=capture["max"].dt.strftime("%Y-%m"))[["capture_min", "capture_max"]]
    quality = data.street_cell.set_index("cell_id")[["n_images", "n_usable"]].join(dates)
    out = out.merge(quality, left_on="cell_id", right_index=True, how="left")

    # year_gap: years between the aerial year and the street capture year (midpoint of earliest and latest); only recorded, not used to filter
    mid_year = (pd.to_datetime(out.capture_min, format="%Y-%m").dt.year
                + pd.to_datetime(out.capture_max, format="%Y-%m").dt.year) / 2
    out["year_gap"] = (out.naip_year.astype(float) - mid_year).abs().astype("float32")

    # n_targets: how many target scores this cell has; cells with none are dropped (no ground truth, can't train); funnel records how many cells are left after each step, right now no cell gets dropped for "no targets at all"
    out["n_targets"] = out[TARGETS].notna().sum(axis=1).astype("int8")
    funnel = [("cells in the dataset", len(data.cells)), ("after round 1 and round 2", len(out))]
    out = out[out.n_targets > 0]
    funnel.append(("has at least one target", len(out)))
    out = out.sort_values(["split", "cell_id"]).reset_index(drop=True)

    # Column groups and order: the training table columns are ordered by group so it's easy to read when opened (the groups also go into meta.json, training picks columns by group)
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

    # Numeric features become float32 (half the space, good enough for training)
    for column in features + ["year_gap"]:
        out[column] = out[column].astype("float32")
    return out, groups, dict(funnel)


# Step 6 (check): all target scores must be between 0 and 1
def validate_table(out: pd.DataFrame):
    values = out[TARGETS].to_numpy()
    if not 0 <= np.nanmin(values) <= np.nanmax(values) <= 1:
        raise ValueError("Targets must be normalized to 0-1.")


# Step 6 (metadata): contents of meta.json, how this training set was made, how many cells, what was removed, conversion curves
def build_metadata(out, groups, funnel, scores, exclusions, flags, csv_hash, config):
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {
        # Name, source dataset, which two label sets, settings record (PreparationConfig.notebook_config), creation time
        "training_id": config.training_id, "dataset_ids": [config.dataset_id],
        "label_id": f"{config.street_id}+{config.aerial_id}+fused",
        "config": config.notebook_config(), "embeddings": {}, "created_at": timestamp, "updated_at": timestamp,
        # Counts: cells left after each step, cells and hexagons in each of Train/Val/Test, cells per area
        "funnel": funnel, "counts": {split: int(out.split.eq(split).sum()) for split in ["train", "val", "test"]},
        "blocks": {split: int(out.loc[out.split.eq(split)].block_id.nunique()) for split in ["train", "val", "test"]},
        "cells_per_city": {city: int(count) for city, count in out.city.value_counts().sort_index().items()},
        "columns": groups, "cities": sorted(out.city.unique().tolist()), "n_cells": len(out),
        # Manual review: how many rows, how many cells, how many each reviewer flagged, CSV hash
        "manual_flags": {"rows": len(flags), "cells": int(flags.cell_id.nunique()),
                         "cells_per_reviewer": flags.groupby("reviewer").cell_id.nunique().to_dict(),
                         "csv_sha256": csv_hash},
        # Conversion: method, curves, mean and std of both sides
        "street_to_aerial_method": "quantile", "street_to_aerial_curve": scores.curves,
        "street_to_aerial_parameters": scores.parameters.round(4).to_dict("index"),
        # Removal: how many cells each rule / each reviewer removed
        "removed_by_rule": exclusions.groupby("rule").cell_id.nunique().to_dict(),
        "notes": "targets are fused street+aerial scores (0 to 1, NaN = not used for that dimension); street scores put on the aerial scale by "
                 + "quantile matching; hilly_terrain is not used",
    }


# Full prep: runs steps 1-6 in order, returns (training table, removal records, meta.json contents) for write_prepared; called by usage 1 in main
def prepare_dataset(source: Inputs, config: PreparationConfig, manual_flags_csv: str | None = None):
    # Steps 1-2: read and check, then split
    data = load_preparation_inputs(source, config)
    cells = assign_splits(data.cells, config)

    # Step 3: round 1 runs the three detection rules live (same code as the "detection only" usage), round 2 is manual review, then remove the hit cells
    spatial = SpatialConfig()
    candidates = detect(*load_inputs(source.root, config.street_id, config.aerial_id, spatial), spatial)
    candidates = candidates[candidates.cell_id.isin(cells.cell_id)]   # cells already dropped in step 1 (unusable aerial tile) don't need removing again
    flags, csv_hash = load_manual_flags(source, manual_flags_csv)
    exclusions = collect_exclusions(cells, candidates, flags)
    removed = set(exclusions.cell_id) #IDs of the cells to remove
    kept = cells.loc[~cells.cell_id.isin(removed)].reset_index(drop=True) #cells left after removal (with split columns), used from here on

    # Steps 4-6: convert + fuse, build the table, check, build the metadata
    scores = fuse_scores(data, kept)
    out, groups, funnel = assemble_table(data, kept, scores, config)
    validate_table(out)
    metadata = build_metadata(out, groups, funnel, scores, exclusions, flags, csv_hash, config)
    LOG.info("Prepared %s cells / %s columns; splits: %s; removed %s cells",
             len(out), len(out.columns), metadata["counts"], len(removed))
    return out, exclusions, metadata


# Step 7: only writes into a new folder, an existing training set is never overwritten
# Default location gs://<bucket>/training/<training-id>/ (built by main), writes 3 files: dataset.parquet, exclusions.parquet, meta.json
def write_prepared(result, output_dir: str):
    out, exclusions, metadata = result #the three things returned by prepare_dataset

    # Writing to the bucket: never into datasets/ (source data stays untouched); error if the folder exists; then write the files one by one
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

    # Writing locally (no bucket, or --output-dir is a local path): create a new folder (error if it exists), write 3 files
    else:
        folder = Path(output_dir)
        folder.mkdir(parents=True, exist_ok=False)
        out.to_parquet(folder / "dataset.parquet", index=False)
        exclusions.to_parquet(folder / "exclusions.parquet", index=False)
        (folder / "meta.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    LOG.info("Saved prepared dataset, metadata and exclusions to %s", output_dir)


# ==================== Command line ====================

# Define the 9 command line args (shown by python cli.py --help)
def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description="GreenPath data processor: detect candidates, or --prepare the training dataset")
    # What to do
    cli.add_argument("--prepare", action="store_true", help="做完整的训练数据准备；不加 = 只检测")
    # Which data to read (bucket name defaults to GREENPATH_BUCKET from .env, passed into the container by docker-shell.sh with --env-file)
    cli.add_argument("--bucket", default=os.environ.get("GREENPATH_BUCKET"))
    cli.add_argument("--dataset-id", default="1026_Full_data")
    cli.add_argument("--street-id", default="street_v7_gemini-2.5-flash", help="街景标签文件夹名（换 prompt / 模型时改）")
    cli.add_argument("--aerial-id", default="aerial_a5_gemini-2.5-flash", help="航拍标签文件夹名（换 prompt / 模型时改）")
    cli.add_argument("--input-root", help="可选：改读本地的数据集快照（测试用）")
    # Where to write
    cli.add_argument("--training-id", help="这版训练集的名字（默认 <dataset-id>_<时间>，保证不重名）")
    cli.add_argument("--output-dir", help="输出文件夹（默认 gs://<bucket>/training/<training-id>；只检测时 gs://<bucket>/candidates/<时间>）")
    cli.add_argument("--manual-flags-csv", help="人工审核 CSV（本地或 gs://）；默认 {数据集}/corrections/v2/manual_flags.csv，找不到就跳过；none = 不用")
    return cli


# Entry point: read args, decide where to read and write, then run usage 1 (--prepare) or usage 2 (detection only)
def main() -> None:
    # Logging: print INFO level; skip the per-file read/write details, keep only summaries
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("fsspec").setLevel(logging.WARNING)
    logging.getLogger("gcsfs").setLevel(logging.WARNING)

    # Read args; need at least a bucket or a local snapshot
    cli = parser()
    args = cli.parse_args()
    if not args.input_root and not args.bucket:
        cli.error("Set --bucket / GREENPATH_BUCKET, or supply --input-root.")

    # Where to read: local snapshot, or gs://<bucket>/datasets/<dataset-id>; this run's ID = UTC time + 8 random characters
    root = args.input_root or f"gs://{args.bucket}/datasets/{args.dataset_id}"
    run = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    # Where to write: results go to the bucket by default; only with no bucket (testing with a local --input-root) do they go to local outputs/dev/
    out_root = f"gs://{args.bucket}" if args.bucket else f"outputs/dev/{run}"

    # Usage 1: full training data prep, writes to gs://<bucket>/training/<training-id>/
    if args.prepare:
        training_id = args.training_id or f"{args.dataset_id}_{run}"
        config = PreparationConfig(dataset_id=args.dataset_id, street_id=args.street_id,
                                   aerial_id=args.aerial_id, training_id=training_id)
        result = prepare_dataset(Inputs(root), config, args.manual_flags_csv)
        write_prepared(result, args.output_dir or f"{out_root}/training/{training_id}")
        return

    # Usage 2: detection only (rule 3 thresholds use the SpatialConfig defaults), writes to gs://<bucket>/candidates/<time>/candidates.parquet
    output = f"{args.output_dir or f'{out_root}/candidates/{run}'}/candidates.parquet"
    config = SpatialConfig()
    loaded = load_inputs(root, args.street_id, args.aerial_id, config)
    candidates = detect(*loaded, config)
    # Write the candidate list; the run settings are saved in the same file (in the Parquet metadata)
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
