"""Freeze existing notebook 01-04 outputs and NAIP tiles as a new input version."""
import json
from contextlib import nullcontext
import fcntl
from pathlib import Path
import shutil
import tarfile
import pandas as pd
from greenpath_model.artifact import digest, prepared_directory, safe_path, seal, verify, write_json


def export_sources(data_root, config_file, output, version):
    data_root = Path(data_root)
    stream = (data_root / ".source.lock").open("r") if (data_root / ".source.lock").exists() else None
    with (stream if stream is not None else nullcontext()):
        if stream is not None:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (data_root / "source-workspace.json").exists():
            meta = json.loads((data_root / "processed/labels_meta.json").read_text())
            if not meta.get("complete") or meta.get("images_pending") != 0:
                raise ValueError("Finish labeling this selected workspace before freezing a training snapshot")
        return _export_sources(data_root, config_file, output, version)


def _export_sources(data_root, config_file, output, version):
    data_root = Path(data_root)
    names = ["cells", "osm_features", "labels_cell", "streetview"]
    source_files = [f"processed/{name}.parquet" for name in names] + ["processed/labels_meta.json"]
    if (data_root / "processed/labels_image.parquet").is_file():
        source_files.append("processed/labels_image.parquet")
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
            "source_workspace_version": json.loads((data_root / "source-workspace.json").read_text())["source_version"] if (data_root / "source-workspace.json").exists() else None,
            "scope": "Frozen notebook 01-04 outputs. Export itself performs no collection or teacher invocation.",
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


def archive_workspace(data_root, output, version, snapshot_dir=None):
    """Archive raw acquisition and paid label caches separately from training inputs."""
    import fcntl
    data_root = Path(data_root).resolve()
    workspace = json.loads((data_root / "source-workspace.json").read_text())
    with (data_root / ".source.lock").open("r") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if snapshot_dir is not None:
            snapshot_dir = Path(snapshot_dir)
            verify(snapshot_dir, "greenpath-processed-snapshot-v1")
            inputs = json.loads((snapshot_dir / "source.json").read_text())["source_files"]
            for relative, checksum in inputs.items():
                if digest(safe_path(data_root, relative)) != checksum:
                    raise ValueError("Workspace no longer matches the frozen training snapshot")
        with prepared_directory(output) as staging:
            with tarfile.open(staging / "workspace.tar", "w") as archive:
                for file in sorted(data_root.rglob("*")):
                    if file.is_symlink():
                        raise ValueError("Source archives may not contain symbolic links")
                    if file.is_file() and file.name != ".source.lock":
                        if file.name.endswith(".part") or file.name.startswith("."):
                            raise ValueError("Unfinished workspace file; finish or review acquisition before archiving")
                        archive.add(file, arcname=file.relative_to(data_root).as_posix(), recursive=False)
            write_json(staging / "archive.json", {"source_version": workspace["source_version"],
                "config_sha256": workspace["config_sha256"], "snapshot_version": version,
                "snapshot_manifest_sha256": digest(snapshot_dir / "manifest.json") if snapshot_dir is not None else None,
                "scope": "Raw images, OSM PBF, processed tables, paid label journal and stage records; no credentials."})
            seal(staging, version, "greenpath-source-archive-v1")
    return {"artifact_version": version, "source_version": workspace["source_version"]}
