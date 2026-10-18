"""Package local CLIP weights and encode images without network model downloads."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import shutil
import sys
import platform
import numpy as np
import pandas as pd
from PIL import Image
from greenpath_model.artifact import digest, prepared_directory, safe_path, seal, verify, write_json

PREPROCESSING = {"crop_size": 224, "crop": "integer_center_crop_no_resize", "color": "RGB",
    "input_scale": "uint8 / 255", "mean": [0.48145466, 0.4578275, 0.40821073],
    "std": [0.26862954, 0.26130258, 0.27577711], "tta": "four_rotations_then_four_rotated_horizontal_flips",
    "aggregation": "mean_of_eight_raw_embeddings", "l2_normalization": False,
    "precision": "float32"}


def export_encoder(checkpoint, output, artifact_version, source_revision):
    checkpoint = Path(checkpoint)
    if not checkpoint.is_file() or checkpoint.suffix != ".safetensors" or not source_revision:
        raise ValueError("A local safetensors checkpoint and its source revision are required")
    with prepared_directory(output) as staging:
        shutil.copyfile(checkpoint, staging / "model.safetensors")
        write_json(staging / "encoder.json", {"artifact_version": artifact_version, "architecture": "ViT-B-16",
            "source": "open_clip: ViT-B-16 / laion2b_s34b_b88k", "source_repository": "laion/CLIP-ViT-B-16-laion2B-s34B-b88K",
            "source_revision": source_revision, "weights_file": "model.safetensors", "weights_sha256": digest(checkpoint),
            "output_dim": 512, "tta": True, "preprocessing": PREPROCESSING,
            "usage": "Image tower only; no text encoding, retrieval, RAG or parameter fine-tuning.",
            "versions": {name: importlib.metadata.version(name) for name in ["torch", "torchvision", "open_clip_torch", "pillow", "safetensors"]},
            "source_provenance_limit": "The notebook embedding metadata did not pin a checkpoint digest. Sample re-encoding checks compatibility with its cache; it cannot prove all historical cache rows used these exact weights."})
        seal(staging, artifact_version, "greenpath-clip-encoder-v1")
    return {"artifact_version": artifact_version, "weights_sha256": digest(checkpoint)}


def load_pixels(data_root, rows, crop_size):
    if not rows:
        raise ValueError("At least one image record is required")
    ids = [r["cell_id"] for r in rows]
    if any(not isinstance(key, str) or not key for key in ids) or len(ids) != len(set(ids)):
        raise ValueError("Image IDs must be nonempty and unique")
    arrays, sources = [], {}
    for record in rows:
        p = safe_path(data_root, record["tile_path"])
        with Image.open(p) as image:
            image = image.convert("RGB")
            w, h = image.size
            if min(w, h) < crop_size:
                raise ValueError("Image is smaller than the declared center crop")
            left, top = (w - crop_size) // 2, (h - crop_size) // 2
            arrays.append(np.asarray(image.crop((left, top, left + crop_size, top + crop_size))))
        sources[record["cell_id"]] = {"tile_path": record["tile_path"], "sha256": digest(p)}
    return np.stack(arrays), sources


def encode(encoder_dir, input_file, data_root, output, artifact_version, device="cpu", batch_size=8, reference=None):
    if batch_size < 1:
        raise ValueError("Batch size must be positive")
    encoder_dir, input_file = Path(encoder_dir), Path(input_file)
    manifest = verify(encoder_dir, "greenpath-clip-encoder-v1")
    if "encoder.json" not in manifest["files"]:
        raise ValueError("Encoder metadata must be checksummed")
    spec = json.loads((encoder_dir / "encoder.json").read_text())
    weights = safe_path(encoder_dir, spec["weights_file"])
    if spec["weights_file"] not in manifest["files"] or digest(weights) != spec["weights_sha256"]:
        raise ValueError("Encoder weights must match their recorded digest")
    if spec["preprocessing"] != PREPROCESSING or spec["architecture"] != "ViT-B-16" or spec["output_dim"] != 512 or not spec["tta"]:
        raise ValueError("Unsupported encoder configuration")
    rows = json.loads(input_file.read_text())
    if not isinstance(rows, list):
        raise ValueError("Image input must be a list of cell_id/tile_path records")
    pixels, sources = load_pixels(data_root, rows, PREPROCESSING["crop_size"])
    # Loading from a verified local path does not consult a model registry or download.
    import torch
    import open_clip
    if device == "mps" and not torch.backends.mps.is_available():
        raise ValueError("Requested MPS device is unavailable")
    if device == "cuda" and not torch.cuda.is_available():
        raise ValueError("Requested CUDA device is unavailable")
    model, _, _ = open_clip.create_model_and_transforms("ViT-B-16", pretrained=str(weights), device=device)
    model.eval()
    mean = torch.tensor(PREPROCESSING["mean"], device=device).view(1, 3, 1, 1)
    std = torch.tensor(PREPROCESSING["std"], device=device).view(1, 3, 1, 1)
    outputs = []
    with torch.inference_mode():
        for i in range(0, len(pixels), batch_size):
            x = torch.from_numpy(pixels[i:i + batch_size]).to(device).permute(0, 3, 1, 2).float() / 255
            x = (x - mean) / std
            views = [torch.rot90(x, k, (2, 3)) for k in range(4)] + [torch.rot90(torch.flip(x, (3,)), k, (2, 3)) for k in range(4)]
            outputs.append(torch.stack([model.encode_image(v) for v in views]).mean(0).float().cpu().numpy())
            print(json.dumps({"stage": "encoding", "completed": min(i + batch_size, len(pixels)), "total": len(pixels)}), file=sys.stderr, flush=True)
    values = np.concatenate(outputs)
    if values.shape != (len(rows), 512) or not np.isfinite(values).all():
        raise ValueError("Invalid encoder output shape or values")
    columns = [f"e{i}" for i in range(512)]
    table = pd.DataFrame(values, columns=columns); table.insert(0, "cell_id", [r["cell_id"] for r in rows])
    validation = None
    if reference is not None:
        ref = pd.read_parquet(reference)
        if ref.cell_id.duplicated().any() or not set(table.cell_id).issubset(set(ref.cell_id)):
            raise ValueError("Reference embeddings have duplicate or missing sample IDs")
        expected = ref.set_index("cell_id").loc[table.cell_id, columns].to_numpy(dtype=np.float32)
        max_error = float(np.max(np.abs(values - expected)))
        # MPS/CPU and batching can cause floating-point differences. This tolerance is
        # declared before running and is not adjusted to force agreement.
        if not np.allclose(values, expected, atol=2e-3, rtol=2e-3):
            raise ValueError(f"Re-encoded images disagree with historical embeddings: {max_error:g}")
        validation = {"reference_sha256": digest(reference), "max_abs_error": max_error,
            "absolute_tolerance": 2e-3, "relative_tolerance": 2e-3, "n_images": len(rows)}
    with prepared_directory(output) as staging:
        table.to_parquet(staging / "embeddings.parquet", index=False)
        write_json(staging / "embedding.json", {"encoder_version": manifest["artifact_version"],
            "encoder_manifest_sha256": digest(encoder_dir / "manifest.json"), "weights_sha256": spec["weights_sha256"],
            "device": device, "batch_size": batch_size, "n_cells": len(rows), "features": columns,
            "runtime_versions": {name: importlib.metadata.version(name) for name in ["torch", "open_clip_torch", "numpy"]}, "platform": platform.platform(),
            "images": sources, "cache_comparison": validation})
        seal(staging, artifact_version, "greenpath-embeddings-v1")
    return {"artifact_version": artifact_version, "n_images": len(rows), "cache_comparison": validation}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    for key in ["checkpoint", "output"]:
        export.add_argument(f"--{key}", type=Path, required=True)
    export.add_argument("--artifact-version", required=True)
    export.add_argument("--source-revision", required=True)
    run = commands.add_parser("encode")
    for key in ["encoder-dir", "input", "data-root", "output"]:
        run.add_argument(f"--{key}", type=Path, required=True)
    run.add_argument("--artifact-version", required=True)
    run.add_argument("--device", choices=["cpu", "mps", "cuda"], default="cpu")
    run.add_argument("--batch-size", type=int, default=8)
    run.add_argument("--reference-embeddings", type=Path)
    a = p.parse_args()
    if a.command == "export":
        result = export_encoder(a.checkpoint, a.output, a.artifact_version, a.source_revision)
    else:
        result = encode(a.encoder_dir, a.input, a.data_root, a.output, a.artifact_version, a.device, a.batch_size, a.reference_embeddings)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
