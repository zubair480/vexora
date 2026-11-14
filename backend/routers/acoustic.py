"""Acoustic anomaly screening endpoints (EXPERIMENTAL, not a diagnosis).

Clips are prerecorded or synthetic WAV files; nothing here records from a band or microphone.

POST /reference and POST /score accept either
- JSON  `{"station_id": "...", "path": "<server-side .wav path>", "source": "prerecorded"}`, or
- multipart form with fields `station_id`, `source` and a WAV upload in `file`
  (needs python-multipart; without it the endpoint answers 415 and JSON still works).
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from .. import acoustic_anomaly

router = APIRouter(prefix="/api/acoustic", tags=["acoustic"])

MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def _multipart_available() -> bool:
    try:
        import python_multipart  # noqa: F401
        return True
    except Exception:
        try:
            import multipart  # noqa: F401
            return True
        except Exception:
            return False


async def _clip_request(request: Request) -> tuple[str, str, Path, Optional[Path]]:
    """Returns (station_id, source, wav_path, temp_path_to_delete)."""
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        if not _multipart_available():
            raise HTTPException(415, "multipart upload is unavailable (python-multipart is not "
                                     "installed); send JSON with a server-side 'path'")
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            raise HTTPException(422, "multipart request needs a WAV upload in the 'file' field")
        data = await upload.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "upload is too large")
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        try:
            tmp.write(data)
        finally:
            tmp.close()
        return (str(form.get("station_id") or ""), str(form.get("source") or ""),
                Path(tmp.name), Path(tmp.name))
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(422, "send JSON {station_id, path, source} or a multipart upload")
    if not isinstance(body, dict):
        raise HTTPException(422, "JSON body must be an object")
    path = body.get("path")
    if not path or not isinstance(path, str):
        raise HTTPException(422, "'path' (server-side WAV file) is required")
    if Path(path).suffix.lower() != ".wav":
        raise HTTPException(422, "'path' must point to a .wav file")
    return str(body.get("station_id") or ""), str(body.get("source") or ""), Path(path), None


async def _run(request: Request, fn):
    station_id, source, path, temp = await _clip_request(request)
    try:
        return fn(station_id, path=path, source=source)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:  # unreadable or non-WAV audio
        raise HTTPException(422, f"could not read audio: {type(exc).__name__}: {exc}")
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


@router.post("/reference")
async def add_reference(request: Request) -> dict:
    """Store a clip of normal operation as a reference for a station."""
    return await _run(request, acoustic_anomaly.add_reference)


@router.post("/score")
async def score(request: Request) -> dict:
    """Score a clip against the station's references (screening only, never a diagnosis)."""
    return await _run(request, acoustic_anomaly.score_clip)


@router.get("/references")
def references(station_id: Optional[str] = None) -> dict:
    items = acoustic_anomaly.list_references(station_id)
    return {
        "experimental": True,
        "min_references": acoustic_anomaly.MIN_REFERENCES,
        "isolation_forest_min_references": acoustic_anomaly.ISOLATION_FOREST_MIN_REFERENCES,
        "count": len(items),
        "references": items,
    }


@router.get("/results")
def results(station_id: Optional[str] = None, limit: int = 200) -> dict:
    items = acoustic_anomaly.list_results(station_id, max(1, min(limit, 1000)))
    return {
        "experimental": True,
        "note": acoustic_anomaly.SCREENING_NOTE,
        "count": len(items),
        "results": items,
    }
