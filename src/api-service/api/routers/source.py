# Code viewer: lets the page show the source code of every backend module (read only).
# Safety: only .py files in the api/ folder are open, the name must be on the allowlist, and ../ can't be used to get out.
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter()

# Location of the api/ folder (this file is in api/routers/, so two levels up)
API_DIR = Path(__file__).resolve().parent.parent


# Allowlist: relative paths of all .py files under api/, e.g. "agent.py", "routers/score.py"
def list_files() -> list[str]:
    return sorted(
        p.relative_to(API_DIR).as_posix()
        for p in API_DIR.rglob("*.py")
        if "__pycache__" not in p.parts
    )


# List the files that can be viewed
@router.get("/source")
def get_source_list() -> dict:
    return {"files": list_files()}


# Return the source code of one file, 404 if it is not on the allowlist
@router.get("/source/{path:path}")
def get_source(path: str) -> dict:
    if path not in list_files():
        raise HTTPException(status_code=404, detail=f"not viewable: {path}")
    return {"path": path, "code": (API_DIR / path).read_text(encoding="utf-8")}
