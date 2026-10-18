"""Small immutable artifact primitives, compatible with the app's GCS file manifest."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tempfile


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def safe_path(root, name):
    root = Path(root)
    p = PurePosixPath(name)
    if not name or p.is_absolute() or ".." in p.parts or "\\" in name or str(p) != name or name == ".":
        raise ValueError(f"Unsafe artifact path: {name}")
    target = root / name
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("Path escapes artifact directory")
    return target


def seal(root, artifact_version, format_version):
    files = {p.relative_to(root).as_posix(): {"sha256": digest(p), "size_bytes": p.stat().st_size}
             for p in sorted(root.rglob("*")) if p.is_file() and p.name != "manifest.json"}
    if not artifact_version or not files:
        raise ValueError("Artifact version and files are required")
    write_json(root / "manifest.json", {"artifact_version": artifact_version,
               "format_version": format_version, "files": files})


def verify(root, expected_format=None):
    root = Path(root)
    m = json.loads((root / "manifest.json").read_text())
    if not m.get("artifact_version") or not isinstance(m.get("files"), dict) or not m["files"]:
        raise ValueError("Invalid artifact manifest")
    if expected_format and m.get("format_version") != expected_format:
        raise ValueError("Unsupported artifact format")
    for name, detail in m["files"].items():
        p = safe_path(root, name)
        if name == "manifest.json" or not p.is_file() or p.stat().st_size != detail.get("size_bytes") or digest(p) != detail.get("sha256"):
            raise ValueError(f"Artifact checksum mismatch: {name}")
    return m


@contextmanager
def prepared_directory(destination):
    """Expose a complete artifact only after successful construction and verification."""
    destination = Path(destination)
    if destination.exists():
        raise ValueError(f"Refusing to overwrite artifact: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".preparing-", dir=destination.parent))
    try:
        yield staging
        verify(staging)
        if destination.exists():
            raise ValueError("Destination appeared during artifact construction")
        staging.chmod(0o755)
        staging.rename(destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
