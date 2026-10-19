"""Explicit entry points for notebook 01-04; never invoked by the training launcher."""
import argparse
import json
from pathlib import Path
import fcntl

from .workflow import run_all, run_stage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["all", "streetview", "tiles", "features", "labeling"])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--max-images", type=int, help="Maximum new images selected for labeling; retries can add requests")
    parser.add_argument("--allow-labeling", action="store_true", help="For all: permit a bounded batch of new paid labels")
    args = parser.parse_args()
    if args.max_images is not None and (args.stage not in ["all", "labeling"] or args.max_images < 0):
        parser.error("--max-images must be nonnegative and is supported only by all or labeling")
    if args.allow_labeling and (args.stage != "all" or args.max_images is None or args.max_images < 1):
        parser.error("--allow-labeling requires all and an explicit positive --max-images limit")
    if args.stage == "all" and not args.allow_labeling and args.max_images not in [None, 0]:
        parser.error("New labels require --allow-labeling")
    if args.stage == "all" and not args.allow_labeling:
        args.max_images = 0
    spec = json.loads(args.config.read_text())
    if not isinstance(spec, dict) or spec.get("format_version") != "greenpath-source-config-v1":
        parser.error("Expected a greenpath-source-config-v1 JSON object")
    options = {"spec": spec, "root": args.data_root.resolve(), "max_images": args.max_images}
    # Hold one lock across the selected stage or the entire combined workflow.
    options["root"].mkdir(parents=True, exist_ok=True)
    with (options["root"] / ".source.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("Another source stage is using this data directory")
        if args.stage == "all":
            run_all(options, allow_labeling=args.allow_labeling)
        else:
            run_stage(args.stage, options)


if __name__ == "__main__":
    main()
