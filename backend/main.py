"""
Endpoints
─────────
POST /api/v1/transcribe            Upload audio → enqueue pipeline → return job_id
GET  /api/v1/jobs/{job_id}         Poll job status + progress
POST /api/v1/transcribe/direct     Synchronous transcription (no Celery, for dev/testing)
GET  /api/v1/health                Liveness check

Audio file lifecycle
────────────────────
1. Flutter POSTs multipart/form-data with the audio file + options.
2. FastAPI writes the file to UPLOAD_DIR/{job_id}_{original_name}.
3. The absolute path is passed as a string argument to the Celery task.
4. The Celery worker picks it up, runs the pipeline, and writes outputs
   to OUTPUT_DIR/{job_id}/.
5. Outputs are served as static files at /outputs/{job_id}/filename.
6. Flutter polls /jobs/{job_id} until state == 'complete', then reads
   the result URLs from the response.

Direct mode (POST /api/v1/transcribe/direct)
────────────────────────────────────────────
Skips Celery entirely. Runs Basic Pitch synchronously in the request
handler. Useful for local development without Redis/Celery running.
Returns the full result immediately (no polling needed).
"""

from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from api.schemas import JobStatusResponse
from seperation.pitch_extraction import transcribe_stem

# ── Directory setup ────────────────────────────────────────────────────────────

UPLOAD_DIR = Path("/tmp/scorescribe/uploads")
OUTPUT_DIR = Path("/tmp/scorescribe/outputs")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(title="ScoreScribe", version="0.1.0", docs_url="/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # restrict to your Flutter app origin in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve output files (MusicXML, PDF, MIDI, stem audio) as static files.
# Flutter reads URLs like http://host/outputs/{job_id}/score.musicxml
app.mount("/outputs", StaticFiles(directory=str(OUTPUT_DIR)), name="outputs")


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/api/v1/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


# ── Async transcription (Celery) ───────────────────────────────────────────────

@app.post("/api/v1/transcribe")
async def transcribe_async(
    audio: UploadFile = File(..., description="Audio file: WAV, MP3, FLAC, AIFF, M4A"),
    separate_stems: bool = Form(True,  description="Run Demucs source separation"),
    instruments: str   = Form("",     description="Comma-separated instrument hints (empty=auto)"),
    output_format: str = Form("musicxml", description="musicxml | midi | pdf"),
    quantize: bool     = Form(True,   description="Quantize to 16th-note grid"),
    onset_threshold: float = Form(0.5, description="Basic Pitch onset sensitivity 0–1"),
    frame_threshold: float = Form(0.3, description="Basic Pitch frame sensitivity 0–1"),
    min_note_length_ms: int = Form(58, description="Minimum note length in ms"),
):
    """
    Upload audio and start an async transcription job.
    Returns a job_id; poll GET /api/v1/jobs/{job_id} for progress.
    """
    _validate_audio_file(audio)

    job_id = str(uuid.uuid4())
    audio_path = _save_upload(audio, job_id)

    # Import here to avoid startup failure if Redis is not running
    try:
        from workers.tasks import run_transcription_pipeline
        run_transcription_pipeline.apply_async(
            args=[job_id, str(audio_path)],
            kwargs={
                "separate_stems": separate_stems,
                "instruments": [i.strip() for i in instruments.split(",") if i.strip()],
                "output_format": output_format,
                "quantize": quantize,
                "onset_threshold": onset_threshold,
                "frame_threshold": frame_threshold,
                "min_note_length_ms": min_note_length_ms,
            },
            task_id=job_id,
        )
    except Exception as e:
        audio_path.unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail=f"Task queue unavailable: {e}")

    return {"job_id": job_id, "created_at": datetime.now(timezone.utc).isoformat(), "filename": audio.filename}


# ── Direct (synchronous) transcription — dev / single-instrument shortcut ─────

@app.post("/api/v1/transcribe/direct")
async def transcribe_direct(
    audio: UploadFile = File(...),
    quantize: bool     = Form(True),
    onset_threshold: float = Form(0.5),
    frame_threshold: float = Form(0.3),
    min_note_length_ms: int = Form(58),
    stem_id: str       = Form("other", description="Stem label hint (used for instrument type)"),
):
    """
    Synchronous transcription — runs Basic Pitch directly in this request.
    No Celery or Redis required. Best for:
      • Local development
      • Single-instrument audio (already separated)
      • Quick demos

    Returns the MIDI file path + note events immediately.
    Blocks until processing is complete (can be slow for long audio).
    """
    _validate_audio_file(audio)

    job_id = str(uuid.uuid4())
    audio_path = _save_upload(audio, job_id)
    output_dir = OUTPUT_DIR / job_id
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        midi_path, note_events = transcribe_stem(
            audio_path=str(audio_path),
            output_dir=str(output_dir),
            stem_id=stem_id,
            quantize=quantize,
            onset_threshold=onset_threshold,
            frame_threshold=frame_threshold,
            min_note_length_ms=min_note_length_ms,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Transcription failed: {e}")

    midi_filename = Path(midi_path).name
    base_url = "http://localhost:8000"  # replace with actual host in production

    return {
        "job_id": job_id,
        "midi_url": f"{base_url}/outputs/{job_id}/{midi_filename}",
        "note_count": len(note_events),
        "note_events": note_events[:20],  # preview first 20 notes; full set in MIDI
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Job status polling ─────────────────────────────────────────────────────────

@app.get("/api/v1/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    """Poll the status of an async transcription job."""
    try:
        from celery.result import AsyncResult
        from workers.celery_app import celery_app
        result = AsyncResult(job_id, app=celery_app)
    except Exception:
        raise HTTPException(status_code=503, detail="Task queue unavailable")

    if result.state == "PENDING":
        return JobStatusResponse(
            job_id=job_id, state="pending", progress=0.0,
            stages=_default_stages(), stems=None, result=None,
        )

    if result.state == "FAILURE":
        error_msg = str(result.info) if result.info else "Unknown error"
        return JobStatusResponse(
            job_id=job_id, state="failed", progress=0.0,
            stages=_default_stages(), stems=None, result=None,
            error=error_msg,
        )

    if result.state == "SUCCESS":
        info = result.result or {}
        return JobStatusResponse(
            job_id=job_id, state="complete", progress=1.0,
            stages=info.get("stages", _default_stages()),
            stems=info.get("stems"),
            result=info,
        )

    # PROGRESS state — task called update_state()
    info = result.info or {}
    return JobStatusResponse(
        job_id=job_id,
        state=info.get("state", "processing"),
        progress=float(info.get("progress", 0.0)),
        stages=info.get("stages", _default_stages()),
        stems=info.get("stems"),
        result=info.get("result"),
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

ALLOWED_EXTENSIONS = {".wav", ".mp3", ".flac", ".aiff", ".aif", ".m4a", ".ogg"}
MAX_FILE_SIZE_MB = 200


def _validate_audio_file(audio: UploadFile) -> None:
    """Raise HTTPException for unsupported file types."""
    if not audio.filename:
        raise HTTPException(status_code=400, detail="No filename provided")
    ext = Path(audio.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{ext}'. "
                   f"Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )


def _save_upload(audio: UploadFile, job_id: str) -> Path:
    """Write uploaded file to UPLOAD_DIR and return its Path."""
    safe_name = Path(audio.filename or "audio.wav").name  # strip any path traversal
    audio_path = UPLOAD_DIR / f"{job_id}_{safe_name}"
    with open(audio_path, "wb") as f:
        shutil.copyfileobj(audio.file, f)
    return audio_path


def _default_stages() -> list[dict]:
    return [
        {"id": "separation",    "label": "Source Separation (Demucs)",       "state": "pending", "progress": 0.0},
        {"id": "transcription", "label": "Pitch Transcription (Basic Pitch)", "state": "pending", "progress": 0.0},
        {"id": "instrument_id", "label": "Instrument Detection",              "state": "pending", "progress": 0.0},
        {"id": "notation",      "label": "Sheet Music Generation",            "state": "pending", "progress": 0.0},
    ]