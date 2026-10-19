"""Immutable GCS artifacts with verified, atomic local caches.

Run from api-service: python -m api.artifacts verify|fetch|publish ...
The manifest is published last; existing objects are never overwritten.
"""
import argparse
import base64
import os
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tempfile
from urllib.parse import quote, urlsplit


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def safe_path(root: Path, name: str) -> Path:
    p = PurePosixPath(name)
    if not name or p.is_absolute() or ".." in p.parts or "\\" in name or str(p) != name or name == ".":
        raise ValueError(f"invalid artifact path: {name!r}")
    target = root / name
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("artifact path escapes its directory")
    return target


def read_manifest(root: Path) -> dict:
    manifest = json.loads((root / "manifest.json").read_text())
    files = manifest.get("files")
    if not manifest.get("artifact_version") or not isinstance(files, dict) or not files:
        raise ValueError("manifest needs artifact_version and a nonempty files map")
    for name, details in files.items():
        safe_path(root, name)
        if name == "manifest.json" or not isinstance(details, dict):
            raise ValueError("invalid manifest entry")
        digest = details.get("sha256", "")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError(f"invalid SHA256: {name}")
        if not isinstance(details.get("size_bytes"), int) or details["size_bytes"] < 0:
            raise ValueError(f"invalid size: {name}")
    return manifest


def verify(root: Path) -> dict:
    root = Path(root)
    manifest = read_manifest(root)
    for name, details in manifest["files"].items():
        p = safe_path(root, name)
        if not p.is_file() or p.stat().st_size != details["size_bytes"] or sha256_file(p) != details["sha256"]:
            raise ValueError(f"artifact checksum/size mismatch: {name}")
    return manifest


class GCS:
    def __init__(self, uri: str):
        parsed = urlsplit(uri)
        if parsed.scheme != "gs" or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("expected gs://bucket/version-prefix")
        self.bucket = parsed.netloc
        self.prefix = parsed.path.strip("/")
        if not self.prefix or ".." in PurePosixPath(self.prefix).parts:
            raise ValueError("use a nonempty, versioned GCS prefix")
        import google.auth
        from google.auth.transport.requests import AuthorizedSession
        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/devstorage.read_write"])
        self.session = AuthorizedSession(creds)

    def get(self, name: str) -> bytes | None:
        object_name = quote(f"{self.prefix}/{name}", safe="")
        response = self.session.get(f"https://storage.googleapis.com/storage/v1/b/{self.bucket}/o/{object_name}", params={"alt": "media"}, timeout=60)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.content

    def create(self, name: str, content: bytes):
        response = self.session.post(
            f"https://storage.googleapis.com/upload/storage/v1/b/{self.bucket}/o",
            params={"uploadType": "media", "name": f"{self.prefix}/{name}", "ifGenerationMatch": "0"},
            data=content, headers={"Content-Type": "application/octet-stream"}, timeout=120,
        )
        if response.status_code in (409, 412):
            if self.get(name) != content:
                raise ValueError(f"refusing to overwrite existing GCS object: {name}")
            return
        response.raise_for_status()


    def metadata(self, name):
        object_name = quote(f"{self.prefix}/{name}", safe="")
        response = self.session.get(f"https://storage.googleapis.com/storage/v1/b/{self.bucket}/o/{object_name}", timeout=60)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def get_file(self, name, destination):
        object_name = quote(f"{self.prefix}/{name}", safe="")
        with self.session.get(f"https://storage.googleapis.com/storage/v1/b/{self.bucket}/o/{object_name}",
                              params={"alt": "media"}, stream=True, timeout=600) as response:
            if response.status_code == 404:
                raise ValueError(f"Missing remote artifact file: {name}")
            response.raise_for_status()
            with Path(destination).open("wb") as stream:
                for chunk in response.iter_content(1024 * 1024):
                    stream.write(chunk)

    def create_file(self, name, path):
        path = Path(path)
        md5 = hashlib.md5()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                md5.update(chunk)
        expected = base64.b64encode(md5.digest()).decode("ascii")

        def matches(metadata):
            return metadata and int(metadata["size"]) == path.stat().st_size and metadata.get("md5Hash") == expected

        existing = self.metadata(name)
        if existing:
            if not matches(existing):
                raise ValueError(f"Refusing to overwrite different GCS contents: {name}")
            return
        with path.open("rb") as stream:
            response = self.session.post(
                f"https://storage.googleapis.com/upload/storage/v1/b/{self.bucket}/o",
                params={"uploadType": "media", "name": f"{self.prefix}/{name}", "ifGenerationMatch": "0"},
                data=stream, headers={"Content-Type": "application/octet-stream"}, timeout=600)
        if response.status_code in (409, 412):
            if not matches(self.metadata(name)):
                raise ValueError(f"Refusing to overwrite different GCS contents: {name}")
        else:
            response.raise_for_status()
            if not matches(response.json()):
                raise ValueError(f"GCS upload checksum differs: {name}")


def publish(root: Path, store) -> dict:
    manifest = verify(root)
    content = (root / "manifest.json").read_bytes()
    existing = store.get("manifest.json")
    if existing is not None and existing != content:
        raise ValueError("GCS version already exists with a different manifest; choose a new version")
    for name in manifest["files"]:
        if hasattr(store, "create_file"):
            store.create_file(name, safe_path(root, name))
        else:
            store.create(name, safe_path(root, name).read_bytes())
    store.create("manifest.json", content)
    return manifest


def fetch(destination: Path, store, merge=False) -> dict:
    content = store.get("manifest.json")
    if content is None:
        raise ValueError("published artifact manifest not found")
    if destination.is_symlink():
        raise ValueError("Refusing a symlink destination")
    if destination.exists() and (not merge or (destination / "manifest.json").exists()):
        manifest = verify(destination)
        if (destination / "manifest.json").read_bytes() != content:
            raise ValueError("cache belongs to a different manifest; choose another cache directory")
        return manifest
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".artifact-", dir=destination.parent))
    try:
        (staging / "manifest.json").write_bytes(content)
        manifest = read_manifest(staging)
        for name in manifest["files"]:
            p = safe_path(staging, name)
            p.parent.mkdir(parents=True, exist_ok=True)
            if hasattr(store, "get_file"):
                store.get_file(name, p)
            else:
                payload = store.get(name)
                if payload is None:
                    raise ValueError(f"missing remote artifact: {name}")
                p.write_bytes(payload)
        verify(staging)
        staging.chmod(0o755)  # readable by the non-root API container
        if destination.exists():
            if not merge:
                raise ValueError("Destination appeared during download")
            # A serving-only import may already occupy part of a run directory.
            # Preflight every collision, then add missing files without replacing any.
            names = [*manifest["files"], "manifest.json"]
            for name in names:
                target = safe_path(destination, name)
                if target.exists() and (not target.is_file() or sha256_file(target) != sha256_file(safe_path(staging, name))):
                    raise ValueError(f"Restore conflicts with a local file: {name}")
            for name in names:
                target = safe_path(destination, name)
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    try:
                        os.link(safe_path(staging, name), target)
                    except FileExistsError:
                        if sha256_file(target) != sha256_file(safe_path(staging, name)):
                            raise ValueError(f"Concurrent restore conflict: {name}")
            verify(destination)
        else:
            staging.rename(destination)
        return manifest
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("verify"); p.add_argument("directory", type=Path)
    for action in ("fetch", "restore", "publish"):
        p = sub.add_parser(action); p.add_argument("uri"); p.add_argument("directory", type=Path)
    args = parser.parse_args()
    if args.action == "verify":
        result = verify(args.directory)
    elif args.action in ("fetch", "restore"):
        result = fetch(args.directory, GCS(args.uri), merge=args.action == "restore")
    else:
        result = publish(args.directory, GCS(args.uri))
    print(json.dumps({"action": args.action, "artifact_version": result["artifact_version"], "files": len(result["files"]), "verified": True}))


if __name__ == "__main__":
    main()
