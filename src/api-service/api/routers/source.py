# 代码展示窗口：让页面直接看到后端每个模块的源代码（只读）。
# 安全：只开放 api/ 文件夹里的 .py 文件，名字必须在白名单里，不能用 ../ 跳出去。
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter()

# api/ 文件夹的位置（这个文件在 api/routers/ 里，往上两层）
API_DIR = Path(__file__).resolve().parent.parent


# 白名单：api/ 下所有 .py 文件的相对路径，例如 "agent.py"、"routers/score.py"
def list_files() -> list[str]:
    return sorted(
        p.relative_to(API_DIR).as_posix()
        for p in API_DIR.rglob("*.py")
        if "__pycache__" not in p.parts
    )


# 列出能看的文件
@router.get("/source")
def get_source_list() -> dict:
    return {"files": list_files()}


# 返回某一个文件的源代码；不在白名单里就 404
@router.get("/source/{path:path}")
def get_source(path: str) -> dict:
    if path not in list_files():
        raise HTTPException(status_code=404, detail=f"not viewable: {path}")
    return {"path": path, "code": (API_DIR / path).read_text(encoding="utf-8")}
