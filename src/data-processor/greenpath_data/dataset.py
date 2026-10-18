"""Extract notebook 05 joins, filtering, spatial splits, buffering and fold assignment."""
import json
import hashlib
from pathlib import Path
import h3
import numpy as np
import pandas as pd
from PIL import Image
from greenpath_model.artifact import digest, prepared_directory, safe_path, seal, verify, write_json


def assign_splits(frame, ratios=(.70, .15, .15), seed=42):
    rng = np.random.default_rng(seed)
    blocks = frame.groupby("block_id").size().rename("n").reset_index()
    total = blocks.n.sum()
    mapping, test_count, val_count = {}, 0, 0
    for index in rng.permutation(len(blocks)):
        block, n = blocks.iloc[index].block_id, blocks.iloc[index].n
        if test_count < total * ratios[2]:
            mapping[block] = "test"; test_count += n
        elif val_count < total * ratios[1]:
            mapping[block] = "val"; val_count += n
        else:
            mapping[block] = "train"
    return frame.block_id.map(mapping)


def apply_buffer(frame, rings=1):
    if rings <= 0:
        return frame.copy()
    test_cells = set(frame.loc[frame.split == "test", "h3_index"])
    keep = [r.split == "test" or not any(n in test_cells for n in h3.grid_disk(r.h3_index, rings)) for r in frame.itertuples()]
    return frame.loc[keep].reset_index(drop=True)


def assign_folds(frame, n_folds=5, seed=42):
    rng = np.random.default_rng(seed + 1)
    blocks = frame[frame.split.isin(["train", "val"])].groupby("block_id").size()
    blocks = blocks.iloc[rng.permutation(len(blocks))].sort_values(ascending=False, kind="stable")
    loads = np.zeros(n_folds, dtype=int)
    mapping = {}
    for block, n in blocks.items():
        fold = int(loads.argmin()); mapping[block] = fold; loads[fold] += n
    return frame.block_id.map(mapping).astype("Int64")


def join_filter(source_dir, dimensions):
    source_dir = Path(source_dir)
    cells, osm, labels = [pd.read_parquet(source_dir / f"processed/{name}.parquet") for name in ["cells", "osm_features", "labels_cell"]]
    for name, frame in [("cells", cells), ("osm", osm), ("labels", labels)]:
        if frame.empty or frame.h3_index.isna().any() or frame.h3_index.duplicated().any() or frame.cell_id.duplicated().any():
            raise ValueError(f"Invalid or duplicate identity in {name}")
    if not cells.cell_id.map(lambda x: isinstance(x, str) and bool(x)).all():
        raise ValueError("Cell IDs must be nonempty strings")
    for frame in [osm, labels]:
        overlap = cells[["cell_id", "h3_index"]].merge(frame[["cell_id", "h3_index"]], on="h3_index")
        if not (overlap.cell_id_x == overlap.cell_id_y).all():
            raise ValueError("Source tables disagree on geographic identity")
    if not cells.h3_index.map(lambda x: h3.is_valid_cell(x) and h3.get_resolution(x) == 10).all():
        raise ValueError("Expected valid resolution-10 H3 cells")
    values = labels[dimensions].to_numpy(dtype=float)
    if np.isinf(values).any() or np.any((values < 0) | (values > 1)):
        raise ValueError("Teacher labels must be in [0,1] or missing")
    osm_cols = [c for c in osm if c not in ["cell_id", "h3_index", "updated_at"]]
    categorical = [c for c in osm_cols if not pd.api.types.is_numeric_dtype(osm[c])]
    tabular = [c for c in osm_cols if c not in categorical]
    std_cols = [f"{d}_std" for d in dimensions]
    frame = cells.drop(columns=["updated_at"], errors="ignore").merge(
        osm.drop(columns=["cell_id", "updated_at"], errors="ignore"), on="h3_index", how="left", validate="one_to_one").merge(
        labels[["h3_index", "n_usable", *dimensions, *std_cols, "prompt_version", "model"]].rename(
            columns={"prompt_version": "label_prompt_version", "model": "label_model"}), on="h3_index", how="left", validate="one_to_one")
    funnel = {"merged_cells": len(frame)}
    for name, mask in [
        ("usable_aerial_image", lambda d: d.tile_ok.fillna(False).astype(bool)),
        ("at_least_one_labeled_target", lambda d: d[dimensions].notna().sum(axis=1) >= 1),
    ]:
        frame = frame[mask(frame)].reset_index(drop=True); funnel[name] = len(frame)
    year = lambda s: pd.to_datetime(s, format="%Y-%m").dt.year
    street_year = (year(frame.capture_min) + year(frame.capture_max)) / 2
    frame["year_gap"] = (frame.naip_year.astype(float) - street_year).abs()
    frame = frame[frame.year_gap <= 3].reset_index(drop=True); funnel["year_gap_at_most_3"] = len(frame)
    frame = frame[frame.road_total_m.notna()].reset_index(drop=True); funnel["osm_features_present"] = len(frame)
    if frame.empty:
        raise ValueError("Filtering removed all cells")
    frame["block_id"] = frame.h3_index.map(lambda x: h3.cell_to_parent(x, 8))
    frame["split"] = assign_splits(frame)
    frame = apply_buffer(frame)
    funnel["after_one_ring_test_buffer"] = len(frame)
    frame["fold"] = assign_folds(frame)
    return frame, tabular, categorical, funnel


def prepare(source_dir, image_root, contract_file, output, artifact_version, mode="smoke"):
    if mode not in ["smoke", "full"]:
        raise ValueError("Mode must be smoke or full")
    source_dir, image_root = Path(source_dir), Path(image_root)
    source_manifest = verify(source_dir, "greenpath-processed-snapshot-v1")
    contract = json.loads(Path(contract_file).read_text())
    if contract["score_range"] != [0, 1]:
        raise ValueError("Notebook labels use the [0,1] contract")
    dimensions = list(contract["dimensions"])
    frame, tabular, categorical, funnel = join_filter(source_dir, dimensions)
    full_counts = {s: int((frame.split == s).sum()) for s in ["train", "val", "test"]}
    if mode == "smoke":
        selected = []
        for split, count in [("train", 32), ("val", 8), ("test", 8)]:
            group = frame[frame.split == split].sort_values("cell_id")
            if len(group) < count:
                raise ValueError("Source is too small for the declared 48-cell smoke split")
            selected.append(group.head(count))
        frame = pd.concat(selected, ignore_index=True)
    counts = {s: int((frame.split == s).sum()) for s in ["train", "val", "test"]}
    if min(counts.values()) < 2 or frame.groupby("block_id").split.nunique().max() != 1:
        raise ValueError(f"Insufficient or leaking spatial splits after filtering/buffering: {counts}. Collect more spatially separated labeled cells; do not weaken the test boundary to make a demo pass.")
    fit = frame[frame.split.isin(["train", "val"])]
    if (fit[dimensions].notna().sum() < 2).any():
        raise ValueError("Too few fitting labels for a target")
    image_records, image_hashes = [], {}
    for row in frame.sort_values("cell_id").itertuples():
        path = safe_path(image_root, row.tile_path)
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            if image.mode != "RGB" or image.size != (256, 256):
                raise ValueError("Expected RGB 256x256 NAIP tiles")
        image_records.append({"cell_id": row.cell_id, "tile_path": row.tile_path})
        image_hashes[row.cell_id] = digest(path)
    identity = {"source_manifest_sha256": digest(source_dir / "manifest.json"),
                "contract_sha256": digest(contract_file), "recipe_sha256": digest(__file__), "mode": mode}
    dataset_version = "dataset-" + mode + "-" + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]
    source_info = json.loads((source_dir / "source.json").read_text()) if (source_dir / "source.json").exists() else {}
    frame["dataset_version"] = dataset_version
    id_cols = ["cell_id", "h3_index", "city", "lat", "lon"]
    split_cols = ["split", "block_id", "fold"]
    image_cols = ["tile_path", "naip_year", "ground_m", "tile_px"]
    quality_cols = ["n_images", "n_usable", "capture_min", "capture_max", "year_gap", *[f"{d}_std" for d in dimensions]]
    provenance_cols = ["label_model", "label_prompt_version", "dataset_version"]
    frame = frame[id_cols + split_cols + image_cols + tabular + categorical + dimensions + quality_cols + provenance_cols].copy()
    for name in tabular + dimensions + [f"{d}_std" for d in dimensions] + ["year_gap"]:
        frame[name] = frame[name].astype("float32")
    frame = frame.sort_values(["split", "cell_id"]).reset_index(drop=True)
    geometry = []
    for row in frame.itertuples():
        ring = [[lon, lat] for lat, lon in h3.cell_to_boundary(row.h3_index)]; ring.append(ring[0])
        geometry.append({"type": "Feature", "id": row.cell_id, "properties": {"cell_id": row.cell_id, "h3_index": row.h3_index, "split": row.split},
                         "geometry": {"type": "Polygon", "coordinates": [ring]}})
    config = json.loads((source_dir / "selected-config.json").read_text())
    config.update({"dataset_version": dataset_version, "n_pool": counts["train"] + counts["val"], "n_test": counts["test"],
        "run_id": "pipeline-refit-fixed-parameters", "selection_source_experiment": config["run_id"],
        "pipeline_note": "Reuse prior alpha/encoder selection; freshly prepared data and newly computed embeddings. This is not historical-v1 prediction reproduction."})
    with prepared_directory(output) as staging:
        folder = staging / "datasets" / dataset_version; folder.mkdir(parents=True)
        frame.to_parquet(folder / "dataset.parquet", index=False)
        for split in ["train", "val", "test"]:
            frame[frame.split == split].to_parquet(folder / f"{split}.parquet", index=False)
        write_json(folder / "schema.json", {"id": id_cols, "split": split_cols, "image": image_cols, "tabular": tabular, "categorical": categorical,
                   "targets": dimensions, "target_std": [f"{d}_std" for d in dimensions], "quality": quality_cols, "provenance": provenance_cols,
                   "notes": "Spatial splits, [0,1] teacher labels and paths relative to the extracted source image cache."})
        write_json(staging / "schema.json", contract)
        write_json(staging / "images.json", image_records)
        write_json(staging / "region.geojson", {"type": "FeatureCollection", "features": geometry})
        write_json(staging / "training-config.json", config)
        report = {"dataset_version": dataset_version, "mode": mode, "counts": counts, "full_filtered_counts": full_counts,
            "funnel": funnel, "source_version": source_manifest["artifact_version"], "snapshot_version": source_manifest["artifact_version"],
            "source_workspace_version": source_info.get("source_workspace_version"), "identity": identity, "source_manifest_sha256": digest(source_dir / "manifest.json"),
            "image_sha256": image_hashes, "parameters": {"block_resolution": 8, "seed": 42, "buffer_rings": 1, "folds": 5, "max_year_gap": 3},
            "scope": "Notebook-05-equivalent joins, filtering and spatial splits over cached notebook-01-04 products. No fresh collection, OSM extraction or teacher calls.",
            "metrics_scope": "Smoke mode is a pipeline check, not a replacement for the historical 188-cell evaluation.",
            "label_validation": "Teacher-generated labels; human agreement remains pending."}
        write_json(staging / "preparation.json", report)
        seal(staging, artifact_version, "greenpath-prepared-data-v1")
    return {"artifact_version": artifact_version, "dataset_version": dataset_version, "counts": counts, "full_filtered_counts": full_counts}
