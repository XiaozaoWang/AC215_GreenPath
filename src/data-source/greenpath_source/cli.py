"""Explicit entry points for notebook 01-04; never invoked by the training launcher."""
import argparse
import json
from pathlib import Path
import runpy
import fcntl


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["streetview", "tiles", "features", "labeling"])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--max-images", type=int, help="Maximum new images selected for labeling; retries can add requests")
    args = parser.parse_args()
    if args.max_images is not None and (args.stage != "labeling" or args.max_images < 0):
        parser.error("--max-images must be nonnegative and is supported only by labeling")
    spec = json.loads(args.config.read_text())
    if not isinstance(spec, dict) or spec.get("format_version") != "greenpath-source-config-v1":
        parser.error("Expected a greenpath-source-config-v1 JSON object")
    options = {"spec": spec, "root": args.data_root.resolve(), "max_images": args.max_images}
    # Hold an OS lock for the whole stage to prevent duplicate paid calls and table races.
    options["root"].mkdir(parents=True, exist_ok=True)
    with (options["root"] / ".source.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("Another source stage is using this data directory")
        runpy.run_module("greenpath_source." + args.stage, run_name="__main__", init_globals={"SOURCE_OPTIONS": options})


if __name__ == "__main__":
    main()
