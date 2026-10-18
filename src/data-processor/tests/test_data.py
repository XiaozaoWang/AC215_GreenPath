import io
import json
import tarfile
from pathlib import Path
import h3
import numpy as np
import pandas as pd
from PIL import Image
import pytest
from greenpath_model.artifact import seal, verify, write_json
from greenpath_data.dataset import apply_buffer, assign_folds, assign_splits, join_filter, prepare
from greenpath_data.snapshot import extract_tiles

TARGETS = ["traffic_intensity", "quietness", "shade", "naturalness", "scenic_appeal", "historic_character", "liveliness", "visual_openness"]


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"; (root / "processed").mkdir(parents=True)
    images = tmp_path / "images"; (images / "processed/tiles").mkdir(parents=True)
    Image.new("RGB", (256, 256), "green").save(images / "processed/tiles/example.png")
    cells = []
    for i in range(96):
        lat, lon = 42 + (i // 12) * .03, -71.5 + (i % 12) * .03
        index = h3.latlng_to_cell(lat, lon, 10)
        cells.append({"cell_id": "BOS_" + index, "h3_index": index, "city": "synthetic", "lat": lat, "lon": lon,
            "tile_path": "processed/tiles/example.png", "tile_ok": True, "naip_year": 2023,
            "capture_min": "2022-01", "capture_max": "2024-01", "n_images": 4, "ground_m": 154, "tile_px": 256})
    frame = pd.DataFrame(cells); frame.to_parquet(root / "processed/cells.parquet", index=False)
    osm = frame[["cell_id", "h3_index"]].copy(); osm["road_total_m"] = 10.; osm["wc_dominant"] = "tree"
    osm.to_parquet(root / "processed/osm_features.parquet", index=False)
    labels = frame[["cell_id", "h3_index"]].copy()
    for target in TARGETS:
        labels[target] = np.linspace(.1, .9, len(labels)); labels[target + "_std"] = .1
    labels["n_usable"] = 4; labels["prompt_version"] = "test"; labels["model"] = "synthetic-teacher"
    labels.to_parquet(root / "processed/labels_cell.parquet", index=False)
    write_json(root / "selected-config.json", {"run_id": "synthetic-prior-selection", "dataset_version": "v1"})
    seal(root, "synthetic-source", "greenpath-processed-snapshot-v1")
    contract = tmp_path / "contract.json"
    write_json(contract, {"schema_version": "synthetic-schema", "score_range": [0, 1], "dimensions": {d: {"description": d} for d in TARGETS}})
    return root, images, contract


def test_smoke_prepare_runs_joins_spatial_splits_and_image_validation(source, tmp_path):
    root, images, contract = source
    out = tmp_path / "prepared"
    result = prepare(root, images, contract, out, "smoke-test")
    assert result["counts"] == {"train": 32, "val": 8, "test": 8}
    verify(out, "greenpath-prepared-data-v1")
    data = pd.read_parquet(out / "datasets/pipeline-smoke-v1/dataset.parquet")
    assert data.groupby("block_id").split.nunique().max() == 1
    assert data[data.split == "test"].fold.isna().all()
    assert len(json.loads((out / "images.json").read_text())) == 48
    assert len(json.loads((out / "region.geojson").read_text())["features"]) == 48
    with pytest.raises(ValueError, match="overwrite"):
        prepare(root, images, contract, out, "smoke-test")


@pytest.mark.parametrize("case", ["duplicate_h3", "wrong_id", "bad_label", "bad_image"])
def test_invalid_upstream_inputs_fail(source, tmp_path, case):
    root, images, contract = source
    if case in ["duplicate_h3", "wrong_id"]:
        p = root / "processed/osm_features.parquet"; frame = pd.read_parquet(p)
        if case == "duplicate_h3": frame.loc[1, "h3_index"] = frame.loc[0, "h3_index"]
        else: frame.loc[0, "cell_id"] = "different-cell"
        frame.to_parquet(p, index=False)
    elif case == "bad_label":
        p = root / "processed/labels_cell.parquet"; frame = pd.read_parquet(p); frame.loc[0, "shade"] = 2; frame.to_parquet(p, index=False)
    else:
        Image.new("RGB", (100, 100)).save(images / "processed/tiles/example.png")
    seal(root, "synthetic-source", "greenpath-processed-snapshot-v1")
    with pytest.raises(ValueError): prepare(root, images, contract, tmp_path / "bad", "bad-output")
    assert not (tmp_path / "bad").exists()


def test_filter_funnel_drops_unusable_images_and_missing_labels(source):
    root, images, contract = source
    p = root / "processed/cells.parquet"; cells = pd.read_parquet(p); cells.loc[0, "tile_ok"] = False; cells.to_parquet(p, index=False)
    p = root / "processed/labels_cell.parquet"; labels = pd.read_parquet(p); labels.loc[1, TARGETS] = np.nan; labels.to_parquet(p, index=False)
    frame, _, _, funnel = join_filter(root, TARGETS)
    assert funnel["merged_cells"] == 96 and funnel["usable_aerial_image"] == 95 and funnel["at_least_one_labeled_target"] == 94
    assert len(frame) == 94


def test_buffer_removes_neighboring_train_cells_but_keeps_test():
    test = h3.latlng_to_cell(42.3, -71.1, 10)
    neighbor = next(x for x in h3.grid_disk(test, 1) if x != test)
    far = h3.latlng_to_cell(42.8, -71.1, 10)
    frame = pd.DataFrame({"h3_index": [test, neighbor, far], "split": ["test", "train", "val"]})
    assert apply_buffer(frame).h3_index.tolist() == [test, far]


@pytest.mark.parametrize("kind", ["traversal", "symlink", "duplicate"])
def test_archive_extraction_rejects_unsafe_members(tmp_path, kind):
    source = tmp_path / "source"; source.mkdir()
    with tarfile.open(source / "tiles.tar", "w") as archive:
        name = "../escape.png" if kind == "traversal" else "processed/tile.png"
        item = tarfile.TarInfo(name); item.size = 4
        if kind == "symlink": item.type = tarfile.SYMTYPE; item.linkname = "/tmp/outside"; item.size = 0
        archive.addfile(item, io.BytesIO(b"test"))
        if kind == "duplicate": archive.addfile(item, io.BytesIO(b"test"))
    seal(source, "unsafe", "greenpath-processed-snapshot-v1")
    with pytest.raises(ValueError): extract_tiles(source, tmp_path / "cache")
    assert not (tmp_path / "cache").exists() and not (tmp_path / "escape.png").exists()
