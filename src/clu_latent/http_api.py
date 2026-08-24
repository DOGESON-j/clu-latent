"""Optional HTTP API surface for CLULatent V1.

This module is intentionally thin: it exposes the existing ingest and
read-only package APIs over HTTP without changing the CLULatent format or
claiming semantic understanding that V1 does not provide.

Install with ``pip install 'clu-latent[api]'`` and run with::

    uvicorn clu_latent.http_api:app --host 0.0.0.0 --port 8000

All non-health endpoints require ``CLULATENT_API_KEY`` unless
``CLULATENT_ALLOW_ANONYMOUS=1`` is explicitly set.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .archive import ARCHIVE_MEDIA_TYPE, ArchiveError, pack_package
from .constants import TOOL_VERSION
from .ingest import ingest_video
from .package_reader import MalformedPackageError, PackageReaderError, open_package

API_VERSION = "0.1.0"
PROFILE = "clulatent.profile.v1"

_DATA_DIR = Path(os.getenv("CLULATENT_DATA_DIR", "/tmp/clulatent-api")).expanduser()
_MAX_UPLOAD_BYTES = int(os.getenv("CLULATENT_MAX_UPLOAD_BYTES", str(100 * 1024 * 1024)))
_MAX_WORKERS = max(1, int(os.getenv("CLULATENT_MAX_WORKERS", "2")))
_MAX_WINDOW_EVENTS = max(1, int(os.getenv("CLULATENT_MAX_WINDOW_EVENTS", "500")))
_MAX_WINDOW_RADIUS_MS = max(0, int(os.getenv("CLULATENT_MAX_WINDOW_RADIUS_MS", "60000")))
_ALLOWED_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
_UPLOAD_CHUNK_BYTES = 1024 * 1024

_DATA_DIR.mkdir(parents=True, exist_ok=True)
_EXECUTOR = ThreadPoolExecutor(max_workers=_MAX_WORKERS, thread_name_prefix="clulatent-api")
_BEARER = HTTPBearer(auto_error=False)
_ARTIFACT_LOCKS: dict[str, threading.Lock] = {}
_ARTIFACT_LOCKS_GUARD = threading.Lock()

app = FastAPI(
    title="CLU Latent API",
    version=API_VERSION,
    description=(
        "HTTP access to CLULatent V1 media-evidence packages. "
        "Evidence is bounded and reviewable; the API does not claim general semantic video understanding."
    ),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _job_dir(job_id: str) -> Path:
    return _DATA_DIR / job_id


def _meta_path(job_id: str) -> Path:
    return _job_dir(job_id) / "job.json"


def _package_path(job_id: str) -> Path:
    return _job_dir(job_id) / "package.clulatent"


def _artifact_path(job_id: str) -> Path:
    return _job_dir(job_id) / "artifact.clulatent"


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read_job(job_id: str) -> dict[str, Any]:
    if not job_id.startswith("job_"):
        raise HTTPException(status_code=404, detail="job not found")
    token = job_id[4:]
    if len(token) != 32 or any(c not in "0123456789abcdef" for c in token):
        raise HTTPException(status_code=404, detail="job not found")
    path = _meta_path(job_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="job not found")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="job metadata is unreadable") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail="job metadata is invalid")
    return data


def _update_job(job_id: str, **changes: Any) -> dict[str, Any]:
    data = _read_job(job_id)
    data.update(changes)
    data["updated_at"] = _utc_now()
    _write_json_atomic(_meta_path(job_id), data)
    return data


def _redact_error(exc: Exception) -> str:
    text = str(exc).replace(str(_DATA_DIR), "<data>")
    return text[:2000]


def _public_summary(job_id: str) -> dict[str, Any]:
    package = _package_path(job_id)
    try:
        with open_package(package) as reader:
            summary = reader.summary()
    except (PackageReaderError, MalformedPackageError, OSError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"package could not be read: {_redact_error(exc)}",
        ) from exc
    summary.pop("path", None)
    return summary


def _auth_required(
    credentials: HTTPAuthorizationCredentials | None = Depends(_BEARER),
) -> None:
    if os.getenv("CLULATENT_ALLOW_ANONYMOUS", "").strip().lower() in {"1", "true", "yes"}:
        return

    expected = os.getenv("CLULATENT_API_KEY", "")
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API authentication is not configured",
        )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not hmac.compare_digest(credentials.credentials, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _artifact_lock(job_id: str) -> threading.Lock:
    with _ARTIFACT_LOCKS_GUARD:
        return _ARTIFACT_LOCKS.setdefault(job_id, threading.Lock())


def _process_job(job_id: str) -> None:
    try:
        meta = _read_job(job_id)
        input_path = _job_dir(job_id) / str(meta["input_file"])
        package = _package_path(job_id)
        _update_job(job_id, status="processing", started_at=_utc_now())
        result = ingest_video(input_path, package)
        with open_package(result.package_path) as reader:
            summary = reader.summary()
            validation = reader.validate()
        summary.pop("path", None)
        validation_payload = {
            "valid": bool(validation.valid),
            "errors": list(validation.errors),
            "warnings": list(validation.warnings),
        }
        _update_job(
            job_id,
            status="completed",
            completed_at=_utc_now(),
            package_id=result.manifest.package_id,
            summary=summary,
            validation=validation_payload,
        )
    except Exception as exc:  # worker boundary: persist a clean failure state
        try:
            _update_job(
                job_id,
                status="failed",
                failed_at=_utc_now(),
                error=_redact_error(exc),
            )
        except Exception:
            pass


@app.get("/v1/health", tags=["system"])
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "clu-latent",
        "api_version": API_VERSION,
        "clulatent_version": TOOL_VERSION,
        "profile": PROFILE,
    }


@app.get(
    "/v1/capabilities",
    tags=["system"],
    dependencies=[Depends(_auth_required)],
)
def capabilities() -> dict[str, Any]:
    return {
        "profile": PROFILE,
        "accepted_video_suffixes": sorted(_ALLOWED_SUFFIXES),
        "max_upload_bytes": _MAX_UPLOAD_BYTES,
        "max_workers": _MAX_WORKERS,
        "max_window_radius_ms": _MAX_WINDOW_RADIUS_MS,
        "semantic_claims": False,
        "outputs": ["package", "summary", "time_window"],
    }


@app.post(
    "/v1/jobs",
    status_code=status.HTTP_202_ACCEPTED,
    tags=["jobs"],
    dependencies=[Depends(_auth_required)],
)
async def create_job(file: UploadFile = File(...)) -> dict[str, Any]:
    original_name = file.filename or "upload"
    suffix = Path(original_name).suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"unsupported video type; allowed: {', '.join(sorted(_ALLOWED_SUFFIXES))}",
        )

    job_id = f"job_{uuid4().hex}"
    root = _job_dir(job_id)
    root.mkdir(parents=True, exist_ok=False)
    input_name = f"input{suffix}"
    input_path = root / input_name
    total = 0

    try:
        with input_path.open("wb") as handle:
            while True:
                chunk = await file.read(_UPLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"upload exceeds {_MAX_UPLOAD_BYTES} bytes",
                    )
                handle.write(chunk)
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise
    finally:
        await file.close()

    if total == 0:
        shutil.rmtree(root, ignore_errors=True)
        raise HTTPException(status_code=400, detail="uploaded file is empty")

    sha256 = hashlib.sha256()
    with input_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_UPLOAD_CHUNK_BYTES), b""):
            sha256.update(chunk)

    created = _utc_now()
    meta = {
        "id": job_id,
        "status": "queued",
        "created_at": created,
        "updated_at": created,
        "input_file": input_name,
        "input_bytes": total,
        "input_sha256": sha256.hexdigest(),
        "profile": PROFILE,
    }
    _write_json_atomic(_meta_path(job_id), meta)
    _EXECUTOR.submit(_process_job, job_id)
    return {"id": job_id, "status": "queued", "profile": PROFILE}


@app.get(
    "/v1/jobs/{job_id}",
    tags=["jobs"],
    dependencies=[Depends(_auth_required)],
)
def get_job(job_id: str) -> dict[str, Any]:
    meta = _read_job(job_id)
    return {k: v for k, v in meta.items() if k != "input_file"}


@app.get(
    "/v1/jobs/{job_id}/summary",
    tags=["evidence"],
    dependencies=[Depends(_auth_required)],
)
def get_summary(job_id: str) -> dict[str, Any]:
    meta = _read_job(job_id)
    if meta.get("status") != "completed":
        raise HTTPException(status_code=409, detail=f"job is {meta.get('status', 'unknown')}")
    return _public_summary(job_id)


@app.get(
    "/v1/jobs/{job_id}/window",
    tags=["evidence"],
    dependencies=[Depends(_auth_required)],
)
def get_window(
    job_id: str,
    at_ms: int = Query(..., ge=0),
    radius_ms: int = Query(2000, ge=0),
) -> dict[str, Any]:
    meta = _read_job(job_id)
    if meta.get("status") != "completed":
        raise HTTPException(status_code=409, detail=f"job is {meta.get('status', 'unknown')}")
    if radius_ms > _MAX_WINDOW_RADIUS_MS:
        raise HTTPException(status_code=400, detail=f"radius_ms exceeds {_MAX_WINDOW_RADIUS_MS}")

    package = _package_path(job_id)
    try:
        with open_package(package) as reader:
            if at_ms > reader.duration_ms:
                raise HTTPException(
                    status_code=400,
                    detail=f"at_ms exceeds media duration ({reader.duration_ms})",
                )
            start_ms = max(0, at_ms - radius_ms)
            end_ms = min(reader.duration_ms, at_ms + radius_ms)
            events = reader.query_time(start_ms, end_ms)
            total = len(events)
            selected = events[:_MAX_WINDOW_EVENTS]
            payload = [event.raw for event in selected]
            return {
                "package_id": reader.package_id,
                "at_ms": at_ms,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "event_count": total,
                "returned_event_count": len(payload),
                "truncated": total > len(payload),
                "events": payload,
            }
    except (PackageReaderError, MalformedPackageError, OSError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"package could not be read: {_redact_error(exc)}",
        ) from exc


@app.get(
    "/v1/jobs/{job_id}/artifact",
    tags=["artifacts"],
    dependencies=[Depends(_auth_required)],
)
def get_artifact(job_id: str) -> FileResponse:
    meta = _read_job(job_id)
    if meta.get("status") != "completed":
        raise HTTPException(status_code=409, detail=f"job is {meta.get('status', 'unknown')}")

    package = _package_path(job_id)
    artifact = _artifact_path(job_id)
    lock = _artifact_lock(job_id)
    with lock:
        if not artifact.is_file():
            try:
                pack_package(package, artifact)
            except ArchiveError as exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"artifact could not be packed: {_redact_error(exc)}",
                ) from exc

    return FileResponse(
        path=artifact,
        media_type=ARCHIVE_MEDIA_TYPE,
        filename=f"{job_id}.clulatent",
    )
