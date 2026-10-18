"""Cached-source ingestion and real dataset preparation for the four-job ML pipeline."""
import argparse
import json
from pathlib import Path
from greenpath_model.artifact import verify
from greenpath_data.dataset import prepare
from greenpath_data.snapshot import export_sources, extract_tiles
from greenpath_data.storage import fetch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("snapshot")
    for key in ["data-root", "config", "output"]:
        export.add_argument(f"--{key}", type=Path, required=True)
    export.add_argument("--artifact-version", required=True)
    process = commands.add_parser("prepare")
    for key in ["source-dir", "image-cache", "contract", "output"]:
        process.add_argument(f"--{key}", type=Path, required=True)
    process.add_argument("--artifact-version", required=True)
    process.add_argument("--mode", choices=["smoke", "full"], default="smoke")
    ingest = commands.add_parser("ingest")
    for key in ["source-uri", "encoder-uri", "source-version", "encoder-version", "artifact-version"]:
        ingest.add_argument(f"--{key}", required=True)
    for key in ["cache", "contract", "output"]:
        ingest.add_argument(f"--{key}", type=Path, required=True)
    ingest.add_argument("--mode", choices=["smoke", "full"], default="smoke")
    ingest.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    if args.command == "snapshot":
        result = export_sources(args.data_root, args.config, args.output, args.artifact_version)
    elif args.command == "prepare":
        images = extract_tiles(args.source_dir, args.image_cache)
        result = prepare(args.source_dir, images, args.contract, args.output, args.artifact_version, args.mode)
    else:
        source = args.cache / "sources" / args.source_version
        encoder = args.cache / "encoders" / args.encoder_version
        for uri, destination, expected, version in [(args.source_uri, source, "greenpath-processed-snapshot-v1", args.source_version),
                   (args.encoder_uri, encoder, "greenpath-clip-encoder-v1", args.encoder_version)]:
            if not args.offline:
                fetch(uri, destination)
            manifest = verify(destination, expected)
            if manifest["artifact_version"] != version:
                raise ValueError("Selected artifact version differs from the manifest")
        images = extract_tiles(source, args.cache / "images" / args.source_version)
        result = prepare(source, images, args.contract, args.output, args.artifact_version, args.mode)
        result["ingestion"] = "verified_local_cache" if args.offline else "gcs_manifest_verified"
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
