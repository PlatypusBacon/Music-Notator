"""
ScoreScribe — FastAPI Backend
═════════════════════════════
Endpoints
─────────
POST /api/v1/transcribe            Upload audio → enqueue pipeline → return job_id
GET  /api/v1/jobs/{job_id}         Poll job status + progress
POST /api/v1/transcribe/direct     Synchronous transcription (no Celery, for dev/testing)
GET  /api/v1/health                Liveness + dependency check
"""

from __future__ import annotations

import logging
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.schemas import JobStatusResponse
from config import settings
from bedug import setup_logging
from seperation.pitch_extraction import transcribe_stem

# ── Logging ────────────────────────────────────────────────────────────────────

setup_logging(level="DEBUG", log_file=Path(__file__).parent / "scorescribe.log")
log = logging.getLogger("scorescribe.api")

# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(title="Score!", version="0.1.0", docs_url="/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/outputs", StaticFiles(directory=str(settings.output_dir)), name="outputs")


# ── Request / response logging middleware ─────────────────────────────────────

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    log.info("→ %s %s  client=%s",
             request.method, request.url.path,
             request.client.host if request.client else "unknown")
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000
    log.info("← %s %s  status=%d  %.1fms",
             request.method, request.url.path, response.status_code, elapsed_ms)
    return response


# ── Startup / shutdown ─────────────────────────────────────────────────────────

@app.on_event("startup")
async def on_startup():
    log.info("=" * 60)
    log.info("ScoreScribe backend starting")
    log.info("  upload_dir : %s", settings.upload_dir)
    log.info("  output_dir : %s", settings.output_dir)
    log.info("  base_url   : %s", settings.base_url)
    log.info("  redis_url  : %s", settings.redis_url)
    log.info("  demucs     : %s", settings.demucs_model)

    # Redis connectivity check
    try:
        import redis as _redis
        _redis.from_url(settings.redis_url, socket_connect_timeout=2).ping()
        log.info("  redis      : ✓ connected")
    except Exception as e:
        log.warning("  redis      : ✗ NOT reachable (%s) — async jobs will fail", e)

    # MuseScore check
    import shutil as _sh
    mscore = _sh.which("mscore3") or _sh.which("mscore") or _sh.which("musescore")
    if mscore:
        log.info("  musescore  : ✓ %s", mscore)
    else:
        log.warning("  musescore  : ✗ not found — PDF export disabled")

    log.info("=" * 60)


@app.on_event("shutdown")
async def on_shutdown():
    log.info("ScoreScribe backend shutting down")


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/api/v1/health")
async def health():
    redis_ok = False
    try:
        import redis as _redis
        _redis.from_url(settings.redis_url, socket_connect_timeout=1).ping()
        redis_ok = True
    except Exception:
        pass

    payload = {
        "status":    "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "redis":     "ok" if redis_ok else "unreachable",
    }
    log.debug("Health: %s", payload)
    return payload


# ── Async transcription (Celery) ───────────────────────────────────────────────

@app.post("/api/v1/transcribe")
async def transcribe_async(
    audio: UploadFile          = File(...),
    separate_stems: bool       = Form(True),
    instruments: str           = Form(""),
    output_format: str         = Form("musicxml"),
    quantize: bool             = Form(True),
    onset_threshold: float     = Form(0.5),
    frame_threshold: float     = Form(0.3),
    min_note_length_ms: int    = Form(58),
):
    _validate_audio_file(audio)
    job_id = str(uuid.uuid4())

    log.info("JOB %s  ── upload received: %s  size=%s  separate=%s",
             job_id, audio.filename, audio.size, separate_stems)

    audio_path = _save_upload(audio, job_id)
    log.debug("JOB %s  ── saved → %s  (%d bytes)",
              job_id, audio_path, audio_path.stat().st_size)

    try:
        from workers.tasks import run_transcription_pipeline
        run_transcription_pipeline.apply_async(
            args=[job_id, str(audio_path)],
            kwargs={
                "do_separate":        separate_stems,
                "instruments":        [i.strip() for i in instruments.split(",") if i.strip()],
                "output_format":      output_format,
                "quantize":           quantize,
                "onset_threshold":    onset_threshold,
                "frame_threshold":    frame_threshold,
                "min_note_length_ms": min_note_length_ms,
            },
            task_id=job_id,
        )
        log.info("JOB %s  ── enqueued in Celery  broker=%s", job_id, settings.redis_url)
    except Exception as e:
        log.error("JOB %s  ── failed to enqueue: %s", job_id, e, exc_info=True)
        audio_path.unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail=f"Task queue unavailable: {e}")

    return {
        "job_id":     job_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "filename":   audio.filename,
    }


# ── Direct synchronous transcription ──────────────────────────────────────────

@app.post("/api/v1/transcribe/direct")
async def transcribe_direct(
    audio: UploadFile          = File(...),
    quantize: bool             = Form(True),
    onset_threshold: float     = Form(0.5),
    frame_threshold: float     = Form(0.3),
    min_note_length_ms: int    = Form(58),
    stem_id: str               = Form("other"),
):
    _validate_audio_file(audio)
    job_id = str(uuid.uuid4())

    log.info("DIRECT %s  ── %s  stem_id=%s", job_id, audio.filename, stem_id)

    audio_path = _save_upload(audio, job_id)
    output_dir = settings.output_dir / job_id
    output_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    try:
        log.debug("DIRECT %s  ── starting Basic Pitch", job_id)
        midi_path, note_events = transcribe_stem(
            audio_path=str(audio_path),
            output_dir=str(output_dir),
            stem_id=stem_id,
            quantize=quantize,
            onset_threshold=onset_threshold,
            frame_threshold=frame_threshold,
            min_note_length_ms=min_note_length_ms,
        )
        elapsed = time.perf_counter() - t0
        log.info("DIRECT %s  ── complete  notes=%d  %.1fs", job_id, len(note_events), elapsed)
    except Exception as e:
        log.error("DIRECT %s  ── failed: %s", job_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Transcription failed: {e}")

    return {
        "job_id":      job_id,
        "midi_url":    settings.output_url(job_id, Path(midi_path).name),
        "note_count":  len(note_events),
        "note_events": note_events[:20],
        "created_at":  datetime.now(timezone.utc).isoformat(),
    }


# ── Job status polling ─────────────────────────────────────────────────────────

@app.get("/api/v1/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    log.debug("POLL %s", job_id)

    try:
        from celery.result import AsyncResult
        from workers.celery_app import celery_app as _celery
        result = AsyncResult(job_id, app=_celery)
    except Exception as e:
        log.error("POLL %s  ── Celery unavailable: %s", job_id, e)
        raise HTTPException(status_code=503, detail="Task queue unavailable")

    log.debug("POLL %s  celery_state=%s", job_id, result.state)

    if result.state == "PENDING":
        return JobStatusResponse(
            job_id=job_id, state="pending", progress=0.0,
            stages=_default_stages(), stems=None, result=None,
        )

    if result.state == "FAILURE":
        error_msg = str(result.info) if result.info else "Unknown error"
        log.warning("POLL %s  ── FAILED: %s", job_id, error_msg)
        return JobStatusResponse(
            job_id=job_id, state="failed", progress=0.0,
            stages=_default_stages(), stems=None, result=None,
            error=error_msg,
        )

    if result.state == "SUCCESS":
        info = result.result or {}
        log.info("POLL %s  ── SUCCESS", job_id)
        return JobStatusResponse(
            job_id=job_id, state="complete", progress=1.0,
            stages=info.get("stages", _default_stages()),
            stems=info.get("stems"),
            result=info,
        )

    # PROGRESS state — task called update_state()
    info = result.info or {}
    progress = float(info.get("progress", 0.0))
    active_stage = next(
        (s["label"] for s in info.get("stages", []) if s.get("state") == "running"),
        "processing",
    )
    log.debug("POLL %s  progress=%.0f%%  stage=%s", job_id, progress * 100, active_stage)

    return JobStatusResponse(
        job_id=job_id,
        state=info.get("state", "processing"),
        progress=progress,
        stages=info.get("stages", _default_stages()),
        stems=info.get("stems"),
        result=info.get("result"),
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

ALLOWED_EXTENSIONS = {".wav", ".mp3", ".flac", ".aiff", ".aif", ".m4a", ".ogg"}


def _validate_audio_file(audio: UploadFile) -> None:
    if not audio.filename:
        raise HTTPException(status_code=400, detail="No filename provided")
    ext = Path(audio.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )
    log.debug("File validated: %s  ext=%s", audio.filename, ext)


def _save_upload(audio: UploadFile, job_id: str) -> Path:
    safe_name = Path(audio.filename or "audio.wav").name
    audio_path = settings.upload_dir / f"{job_id}_{safe_name}"
    with open(audio_path, "wb") as f:
        shutil.copyfileobj(audio.file, f)
    log.debug("Saved upload: %s", audio_path)
    return audio_path


def _default_stages() -> list[dict]:
    return [
        {"id": "separation",    "label": "Source Separation (Demucs)",       "state": "pending", "progress": 0.0},
        {"id": "transcription", "label": "Pitch Transcription (Basic Pitch)", "state": "pending", "progress": 0.0},
        {"id": "instrument_id", "label": "Instrument Detection",              "state": "pending", "progress": 0.0},
        {"id": "notation",      "label": "Sheet Music Generation",            "state": "pending", "progress": 0.0},
    ]