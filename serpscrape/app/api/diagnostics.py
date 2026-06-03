from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.auth import require_token
from app.scrapers.diagnostics import DIAG_DIR

router = APIRouter(prefix="/api/tasks", tags=["diagnostics"])

_MEDIA = {".html": "text/html", ".png": "image/png"}


def _task_dir(task_id: int) -> str:
    return os.path.join(DIAG_DIR, f"task_{task_id}")


@router.get("/{task_id}/diagnostics", dependencies=[Depends(require_token)])
async def list_diagnostics(task_id: int) -> dict:
    d = _task_dir(task_id)
    if not os.path.isdir(d):
        return {"files": []}
    files = []
    for name in sorted(os.listdir(d)):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            files.append({"name": name, "size": os.path.getsize(p)})
    return {"files": files}


@router.get("/{task_id}/diagnostics/{filename}", dependencies=[Depends(require_token)])
async def get_diagnostic(task_id: int, filename: str) -> FileResponse:
    # Reject anything that isn't a bare filename (path-traversal guard).
    if filename != os.path.basename(filename) or filename.startswith("."):
        raise HTTPException(400, "Invalid filename")
    path = os.path.join(_task_dir(task_id), filename)
    if not os.path.isfile(path):
        raise HTTPException(404, "Diagnostic file not found")
    ext = os.path.splitext(filename)[1].lower()
    return FileResponse(path, media_type=_MEDIA.get(ext, "application/octet-stream"))
