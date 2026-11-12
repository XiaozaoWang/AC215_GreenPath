"""Restore a fixed GCS knowledge release, then serve Chroma inside Cloud Run.

GCS holds the durable source of truth. Local Chroma files are disposable and
are rebuilt from stored vectors at every new instance startup, without Vertex.
The RAG API never exposes database write endpoints.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tarfile
from urllib.parse import quote, urlparse

import chromadb
import google.auth
from google.auth.transport.requests import AuthorizedSession

import cli


def download_index(uri, destination, expected_sha256):
    parsed = urlparse(uri)
    if parsed.scheme != "gs" or not parsed.netloc or not parsed.path.strip("/"):
        raise ValueError("RAG_INDEX_URI must identify a gs://bucket/object")
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/devstorage.read_only"])
    url = "https://storage.googleapis.com/storage/v1/b/" + quote(parsed.netloc, safe="")
    url += "/o/" + quote(parsed.path.lstrip("/"), safe="") + "?alt=media"
    with AuthorizedSession(credentials) as session:
        response = session.get(url, timeout=60)
        response.raise_for_status()
        data = response.content
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError("GCS index archive checksum mismatch")
    Path(destination).write_bytes(data)


def restore_index(archive, release_dir, database_dir, expected_count):
    release_dir = Path(release_dir)
    release_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as package:
        members = package.getmembers()
        if sum(member.size for member in members) > 100 * 1024 * 1024:
            raise ValueError("Index archive exceeds the deployment size limit")
        for member in members:
            parts = Path(member.name).parts
            if (not member.isfile() or Path(member.name).is_absolute()
                    or ".." in parts or parts[0] not in ("outputs", "input-datasets")):
                raise ValueError("Unsafe or unexpected index archive entry")
        package.extractall(release_dir, filter="data")
    cli.INPUT_FOLDER = str(release_dir / "input-datasets")
    cli.OUTPUT_FOLDER = str(release_dir / "outputs")
    manifest = cli.checked_manifest("markdown-split", embedded=True)
    if manifest["total_chunks"] != expected_count:
        raise ValueError("Index release count does not match deployment configuration")
    client = chromadb.PersistentClient(path=str(database_dir))
    try:
        cli.load("markdown-split", client=client)
        count = client.get_collection("greenpath-docs-markdown-split").count()
        if count != expected_count:
            raise ValueError("Restored database count does not match the index release")
        print(json.dumps({"severity": "INFO", "message": "index_restored", "count": count,
                          "embedding_model": cli.EMBEDDING_MODEL,
                          "dimension": cli.EMBEDDING_DIMENSION}), flush=True)
    finally:
        client.close()
    return count


def main():
    root = Path(os.getenv("RAG_DATABASE_ROOT", "/data"))
    root.mkdir(parents=True, exist_ok=True)
    archive = root / "index-data.tar.gz"
    download_index(os.environ["RAG_INDEX_URI"], archive, os.environ["RAG_INDEX_SHA256"])
    restore_index(archive, root / "release", root / "chroma", int(os.environ["RAG_EXPECTED_COUNT"]))
    # A new interpreter/native process must open SQLite/HNSW after the loader closes it.
    executable = str(Path(sys.executable).with_name("chroma"))
    os.execv(executable, [executable, "run", "--path", str(root / "chroma"),
                         "--host", "0.0.0.0", "--port", os.getenv("CHROMA_PORT", "8000")])


if __name__ == "__main__":
    main()
