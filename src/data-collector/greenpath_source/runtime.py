"""Configuration, atomic local files, workspace ownership and stage provenance."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile


def digest(file):
    h = hashlib.sha256()
    with Path(file).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


@contextmanager
def atomic_open(file, mode="wb"):
    target = Path(file)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=target.parent, prefix="." + target.name + ".")
    os.close(fd)
    try:
        with open(temporary, mode) as stream:
            yield stream
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_json(file, value):
    with atomic_open(file, "w") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=True, allow_nan=False)


class LocalFiles:
    """Minimal local implementation of the notebook file operations."""
    @staticmethod
    def exists(file):
        return Path(file).exists()

    @staticmethod
    def open(file, mode="rb"):
        return atomic_open(file, mode) if "w" in mode else open(file, mode)


def context(stage, options):
    import pandas as pd
    spec = options["spec"]
    version = spec.get("source_version", "")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", version):
        raise ValueError("source_version must use 1-64 lowercase letters, digits or hyphens")
    expected = {"format_version", "source_version", "streetview", "tiles", "features", "labeling"}
    if set(spec) != expected or any(not isinstance(spec[k], dict) for k in expected - {"format_version", "source_version"}):
        raise ValueError("Configuration must contain exactly the four documented stage sections")
    root = Path(options["root"])
    root.mkdir(parents=True, exist_ok=True)
    marker = root / "source-workspace.json"
    spec_hash = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
    if marker.exists():
        old = json.loads(marker.read_text())
        if old["source_version"] != version or old["config_sha256"] != spec_hash:
            raise ValueError("Workspace belongs to another configuration. Use a new source version and data directory.")
    elif stage != "streetview" or any(p.name != ".source.lock" for p in root.iterdir()):
        raise ValueError("Start streetview in an empty dedicated directory; do not use the historical research data root")
    allowed = {
        "streetview": {"city", "city_prefix", "bbox", "h3_res", "tile_zoom", "min_quality", "min_year", "exclude_months", "allow_pano", "min_images_per_cell", "max_images_per_cell", "sample_size", "seed", "download_images", "download_workers"},
        "tiles": {"tile_px", "ground_m", "naip_min_year", "max_nodata_frac", "seed"},
        "features": {"ground_m", "osm_pad_deg", "worldcover_year", "osm_pbf_url"},
        "labeling": {"prompt_version", "model", "thinking_budget", "workers", "min_usable_images", "max_images", "qa_n", "thumb_px", "gallery_max_images", "seed", "price_in_per_m", "price_out_per_m"},
    }
    for section, keys in allowed.items():
        if set(spec[section]) != keys:
            raise ValueError(f"Unexpected or missing keys in {section}; use the documented configuration template")
    conf = spec[stage]
    limits = {"streetview": {"sample_size", "download_workers", "min_images_per_cell", "max_images_per_cell"},
              "tiles": {"tile_px", "ground_m"}, "features": {"ground_m"},
              "labeling": {"workers", "min_usable_images", "qa_n", "thumb_px"}}
    for key in limits[stage]:
        if isinstance(conf.get(key), bool) or not isinstance(conf.get(key), int) or conf[key] < (0 if key in {"sample_size", "qa_n"} else 1):
            raise ValueError(f"Invalid nonnegative/positive integer configuration: {key}")
    if stage == "streetview":
        if conf["min_images_per_cell"] > conf["max_images_per_cell"] or conf["tile_zoom"] != 14 or not conf["download_images"]:
            raise ValueError("Require downloads, zoom 14 and min_images_per_cell <= max_images_per_cell")
        if conf["h3_res"] != 10:
            raise ValueError("The downstream dataset contract currently requires H3 resolution 10")
    if stage == "streetview":
        bbox = conf["bbox"]
        if len(bbox) != 4 or not (-180 <= bbox[0] < bbox[2] <= 180 and -85 < bbox[1] < bbox[3] < 85):
            raise ValueError("Expected a valid west/south/east/north bbox within Web Mercator coverage")
        existing_cells = root / "processed/cells.parquet"
        if existing_cells.exists() and "tile_path" in pd.read_parquet(existing_cells).columns:
            raise ValueError("Streetview would reset downstream cell columns. Start a new workspace to recollect.")
    if stage != "streetview" and not (root / "processed/cells.parquet").is_file():
        raise ValueError("Missing cells.parquet; run streetview first")
    if stage == "features" and "tile_ok" not in pd.read_parquet(root / "processed/cells.parquet").columns:
        raise ValueError("Run tiles before computing geographic features")
    if stage == "tiles":
        if conf["tile_px"] != 256 or not 0 <= conf["max_nodata_frac"] <= 1:
            raise ValueError("Require 256px tiles and a nodata threshold in [0,1]")
    if stage == "features" and not conf["osm_pbf_url"].startswith("https://"):
        raise ValueError("Use an HTTPS OSM snapshot URL")
    if stage == "labeling":
        if not re.fullmatch(r"[A-Za-z0-9_-]+", conf["prompt_version"]):
            raise ValueError("Unsafe prompt version")
        for price in [conf["price_in_per_m"], conf["price_out_per_m"]]:
            if price is not None and (isinstance(price, bool) or not isinstance(price, (int, float)) or price < 0):
                raise ValueError("Token prices must be nonnegative numbers or null")
        if conf["gallery_max_images"] is not None and (not isinstance(conf["gallery_max_images"], int) or conf["gallery_max_images"] < 1):
            raise ValueError("gallery_max_images must be positive or null")
        maximum = options["max_images"] if options["max_images"] is not None else conf["max_images"]
        if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 0:
            raise ValueError("Set a finite max_images request limit; 0 aggregates cached results without API calls")
    else:
        maximum = None
    if not marker.exists():
        write_json(marker, {"source_version": version, "config_sha256": spec_hash, "configuration": spec})
    for directory in ["processed", "runs", "raw"]:
        (root / directory).mkdir(exist_ok=True)

    def path(*parts):
        target = root.joinpath(*parts).resolve()
        if not target.is_relative_to(root):
            raise ValueError("Data path escapes the dedicated workspace")
        return str(target)

    def read_parquet(file):
        return pd.read_parquet(file)

    def write_parquet(frame, file):
        with atomic_open(file) as stream:
            frame.to_parquet(stream, index=False)

    def now_iso():
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def manifest(value, name):
        outputs = {}
        for relative in value.get("outputs", []):
            directory = Path(path(relative))
            if directory.is_dir():
                for file in sorted(directory.rglob("*")):
                    if file.is_file():
                        outputs[str(file.relative_to(root))] = digest(file)
        for relative in value.get("outputs", []):
            file = Path(path(relative))
            if file.is_file():
                outputs[relative] = digest(file)
        value.update({"source_version": version, "config_sha256": spec_hash,
                      "configuration": conf, "output_sha256": outputs,
                      "implementation_sha256": digest(Path(__file__).with_name(stage + ".py")),
                      "notebook_sources": json.loads(Path(__file__).with_name("notebook-provenance.json").read_text()),
                      "execution": "standalone_python", "human_validation": "pending"})
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        write_json(root / "runs" / stamp / name, value)
        return value

    input_names = {"streetview": [], "tiles": ["processed/cells.parquet"],
                   "features": ["processed/cells.parquet"],
                   "labeling": ["processed/cells.parquet", "processed/streetview.parquet", "processed/osm_features.parquet"]}[stage]
    input_hashes = {name: digest(root / name) for name in input_names if (root / name).is_file()}
    original_manifest = manifest

    def manifest(value, name):
        value["input_sha256"] = input_hashes
        return original_manifest(value, name)

    env = {k.upper(): v for k, v in conf.items()}
    env.update({"ROOT": str(root), "STORAGE": "local", "STORAGE_OPTS": {}, "fs": LocalFiles(),
                "path": path, "makedirs": lambda p: Path(p).mkdir(parents=True, exist_ok=True),
                "read_parquet": read_parquet, "write_parquet": write_parquet,
                "write_manifest": manifest, "now_iso": now_iso,
                "os": os, "io": io, "json": json, "datetime": __import__("datetime"), "pd": pd,
                "PROMPT_DIR": str(Path(__file__).resolve().parents[1] / "prompts"),
                "MAX_IMAGES": maximum, "safe_write_json": write_json, "file_digest": digest})
    print(json.dumps({"stage": stage, "source_version": version, "data_root": str(root)}), flush=True)
    return env
