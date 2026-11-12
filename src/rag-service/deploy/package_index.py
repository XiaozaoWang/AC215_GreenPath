"""Build a deterministic, checksum-validated archive of an existing index."""
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile


def package_index(source, archive):
    source, archive = Path(source), Path(archive)
    manifest_bytes = (source / "outputs/manifest-markdown-split.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    files = {"outputs/manifest-markdown-split.json": manifest_bytes}
    actual_inputs = {str(path.relative_to(source / "input-datasets"))
                     for path in (source / "input-datasets").rglob("*.md")}
    if actual_inputs != set(manifest["inputs"]):
        raise ValueError("Source documents do not match the index manifest")
    for group, folder in [("inputs", "input-datasets"), ("chunks", "outputs"), ("embeddings", "outputs")]:
        for name, expected in manifest[group].items():
            path = Path(folder) / name
            if Path(name).is_absolute() or ".." in Path(name).parts:
                raise ValueError("Unsafe manifest filename")
            content = (source / path).read_bytes()
            if hashlib.sha256(content).hexdigest() != expected:
                raise ValueError("Checksum mismatch: " + str(path))
            files[str(path)] = content
    with archive.open("wb") as output, gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as package:
            for name, content in sorted(files.items()):
                member = tarfile.TarInfo(name)
                member.mode = 0o644
                member.size = len(content)
                package.addfile(member, io.BytesIO(content))
    return hashlib.sha256(archive.read_bytes()).hexdigest(), manifest
