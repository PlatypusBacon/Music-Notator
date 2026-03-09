"""
api/schemas.py
Pydantic models that define the JSON contract between FastAPI and Flutter.
Must stay in sync with transcription_job.dart on the Flutter side.
"""

from __future__ import annotations
from typing import Any, Optional
from pydantic import BaseModel, Field


# ── Stage ──────────────────────────────────────────────────────────────────────

class StageInfo(BaseModel):
    id: str                      # 'separation' | 'transcription' | 'instrument_id' | 'notation'
    label: str
    state: str                   # 'pending' | 'running' | 'complete' | 'failed'
    progress: float = Field(ge=0.0, le=1.0)


# ── Stem ───────────────────────────────────────────────────────────────────────

class StemInfo(BaseModel):
    id: str
    label: str
    audio_url: str


# ── Instrument ────────────────────────────────────────────────────────────────

class DetectedInstrument(BaseModel):
    name: str
    stem_label: str
    confidence: float = Field(ge=0.0, le=1.0)
    note_count: int = Field(ge=0)
    emoji: str = "🎵"


# ── Result ────────────────────────────────────────────────────────────────────

class TranscriptionResult(BaseModel):
    musicxml_url: str
    pdf_url: str
    midi_url: str
    detected_instruments: list[DetectedInstrument]


# ── Job status (response to GET /jobs/{id}) ───────────────────────────────────

class JobStatusResponse(BaseModel):
    job_id: str
    state: str            # 'pending' | 'processing' | 'complete' | 'failed'
    progress: float       # 0.0–1.0
    stages: list[Any]     # list[StageInfo] — Any for dict compatibility during dev
    stems: Optional[list[Any]] = None
    result: Optional[Any] = None
    error: Optional[str] = None  # populated when state == 'failed'


# ── Direct transcription response (POST /transcribe/direct) ──────────────────

class NoteEvent(BaseModel):
    start: float       # seconds
    end: float         # seconds
    pitch: int         # MIDI note number 0–127
    velocity: int      # 0–127
    confidence: float  # 0–1


class DirectTranscriptionResponse(BaseModel):
    job_id: str
    midi_url: str
    note_count: int
    note_events: list[NoteEvent]   # preview of first 20 notes
    created_at: str