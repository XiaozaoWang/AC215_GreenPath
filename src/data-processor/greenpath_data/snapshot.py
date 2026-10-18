"""Freeze existing notebook 01-04 outputs and NAIP tiles as a new input version."""
import json
from pathlib import Path
import shutil
import tarfile
import pandas as pd
from greenpath_model.artifact import digest, prepared_directory, safe_path, seal, verify, write_json


def export_sources(data_root, config_file, output, version):
    data_root = Path(data_root)
    names = ["cells", "osm_features", "labels_cell", "streetview"]
    source_files = [f"processed/{name}.parquet" for name in names] + ["processed/labels_meta.json"]
    cells = pd.read_parquet(data_root / "processed/cells.parquet")
    if cells.cell_id.duplicated().any() or cells.h3_index.duplicated().any():
        raise ValueError("Duplicate source cell identity")
    paths = sorted(set(cells.loc[cells.tile_ok.fillna(False).astype(bool), "tile_path"]))
    source_audit = {}
    historical = data_root / "datasets/v1/manifest.json"
    if historical.exists():
        for name, expected in json.loads(historical.read_text())["inputs"].items():
            actual = digest(safe_path(data_root, name))
            source_audit[name] = {"historical_sha256_prefix": expected, "current_sha256": actual, "matches_historical": actual.startswith(expected)}
    with prepared_directory(output) as staging:
        for name in source_files:
            target = safe_path(staging, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(safe_path(data_root, name), target)
        shutil.copyfile(config_file, staging / "selected-config.json")
        # Preserve the optional upstream extraction provenance with a frozen training snapshot.
        if (data_root / "source-workspace.json").is_file():
            upstream = staging / "upstream"
            upstream.mkdir()
            shutil.copyfile(data_root / "source-workspace.json", upstream / "source-workspace.json")
            for record in sorted((data_root / "runs").glob("*/0*_manifest.json")):
                target = upstream / "runs" / record.parent.name / record.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(record, target)
        with tarfile.open(staging / "tiles.tar", "w") as archive:
            for name in paths:
                path = safe_path(data_root, name)
                if path.suffix.lower() != ".png" or not path.is_file():
                    raise ValueError("A selected aerial PNG is missing")
                archive.add(path, arcname=name, recursive=False)
        write_json(staging / "source.json", {"artifact_version": version, "n_cells": len(cells), "n_images": len(paths),
            "source_files": {name: digest(data_root / name) for name in source_files},
            "selected_config_sha256": digest(config_file), "historical_v1_audit": source_audit,
            "scope": "Cached notebook 01 street-view selection, 02 NAIP tiles, 03 geographic features and 04 teacher labels; no live collection or teacher invocation.",
            "label_validation": "Teacher-generated labels; human annotation agreement remains pending.",
            "image_source": "USDA NAIP via Microsoft Planetary Computer; aerial imagery only. Street-view photos are not redistributed."})
        seal(staging, version, "greenpath-processed-snapshot-v1")
    return {"artifact_version": version, "n_cells": len(cells), "n_images": len(paths)}


def extract_tiles(source_dir, destination):
    source_dir = Path(source_dir)
    verify(source_dir, "greenpath-processed-snapshot-v1")
    source_hash = digest(source_dir / "tiles.tar")
    destination = Path(destination)
    if destination.exists():
        verify(destination, "greenpath-tile-cache-v1")
        if json.loads((destination / "source.json").read_text())["archive_sha256"] != source_hash:
            raise ValueError("Extracted tile cache belongs to another input version")
        return destination
    with prepared_directory(destination) as staging:
        seen, total = set(), 0
        with tarfile.open(source_dir / "tiles.tar", "r") as archive:
            for member in archive:
                target = safe_path(staging, member.name)
                if not member.isfile() or member.issym() or member.islnk() or target.suffix.lower() != ".png" or member.name in seen:
                    raise ValueError("Archive must contain unique regular PNG files only")
                total += member.size
                if member.size > 16 * 1024 * 1024 or total > 2 * 1024 ** 3 or len(seen) >= 100000:
                    raise ValueError("Tile archive exceeds declared safety limits")
                seen.add(member.name)
                target.parent.mkdir(parents=True, exist_ok=True)
                stream = archive.extractfile(member)
                with target.open("wb") as output:
                    shutil.copyfileobj(stream, output)
        if not seen:
            raise ValueError("Tile archive is empty")
        write_json(staging / "source.json", {"archive_sha256": source_hash, "n_images": len(seen)})
        seal(staging, "tiles-" + source_hash[:16], "greenpath-tile-cache-v1")
    return destination
