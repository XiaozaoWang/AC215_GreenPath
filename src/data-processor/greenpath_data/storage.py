"""Read private GCS artifacts through ADC; expose only verified local cache directories."""
import json
from pathlib import Path
from urllib.parse import quote, urlsplit
from greenpath_model.artifact import prepared_directory, verify, safe_path


def fetch(uri, destination):
    import google.auth
    from google.auth.transport.requests import AuthorizedSession
    parsed = urlsplit(uri)
    if parsed.scheme != "gs" or not parsed.netloc or not parsed.path.strip("/") or parsed.query or parsed.fragment:
        raise ValueError("Expected a versioned gs://bucket/prefix URI")
    prefix = parsed.path.strip("/")
    if ".." in prefix.split("/"):
        raise ValueError("Unsafe GCS prefix")
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/devstorage.read_only"])
    session = AuthorizedSession(creds)

    def get(name):
        key = quote(prefix + "/" + name, safe="")
        response = session.get(f"https://storage.googleapis.com/storage/v1/b/{parsed.netloc}/o/{key}", params={"alt": "media"}, timeout=180)
        response.raise_for_status()
        return response.content

    content = get("manifest.json")
    destination = Path(destination)
    if destination.exists():
        verify(destination)
        if (destination / "manifest.json").read_bytes() != content:
            raise ValueError("Existing cache does not match the published version")
        return destination
    manifest = json.loads(content)
    if not isinstance(manifest.get("files"), dict) or not manifest["files"]:
        raise ValueError("Missing artifact file manifest")
    with prepared_directory(destination) as staging:
        (staging / "manifest.json").write_bytes(content)
        for name in manifest["files"]:
            if name == "manifest.json":
                raise ValueError("Manifest cannot include itself")
            target = safe_path(staging, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(get(name))
    return destination
