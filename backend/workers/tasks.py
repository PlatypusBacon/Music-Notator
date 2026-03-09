"""
workers/tasks.py
────────────────
Main Celery task: orchestrates the four-stage transcription pipeline.

Stages
──────
1. separation    → Demucs splits audio into stems (or skips if single-instrument)
2. transcription → Basic Pitch runs on each pitched stem; onset detection on drums
3. instrument_id → stem-label priors + optional YAMNet classification
4. notation      → music21 merges MIDIs → MusicXML + PDF + combined MIDI

Progress updates
────────────────
The task calls self.update_state(state="PROGRESS", meta={...}) after each
significant step. FastAPI's GET /jobs/{id} reads result.info to return live
progress to the Flutter app's polling loop.

Note: the function parameter is named `do_separate` internally to avoid
shadowing the imported `separate_stems` function from demucs_separator.
"""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any

from workers.celery_app import celery_app
from pipeline.separation.demucs_separator import separate_stems
from pipeline.transcription.basic_pitch_runner import transcribe_stem
from pipeline.transcription.instrument_classifier import classify_instrument
from pipeline.notation.score_builder import build_score
from config import settings


# ── Progress helper ────────────────────────────────────────────────────────────

def _push(
    task,
    state: str,
    progress: float,
    stages: list[dict],
    stems: list[dict] | None = None,
    result: dict | None = None,
    error: str | None = None,
) -> None:
    """Publish a PROGRESS update so the API can relay live status to Flutter."""
    meta: dict[str, Any] = {
        "state":    state,
        "progress": round(progress, 3),
        "stages":   stages,
        "stems":    stems,
        "result":   result,
    }
    if error:
        meta["error"] = error
    task.update_state(state="PROGRESS", meta=meta)


# ── Pipeline task ──────────────────────────────────────────────────────────────

@celery_app.task(
    bind=True,
    name="run_transcription_pipeline",
    max_retries=0,          # don't auto-retry expensive ML jobs
    soft_time_limit=600,    # 10 min soft limit → SoftTimeLimitExceeded
    time_limit=660,         # 11 min hard kill
)
def run_transcription_pipeline(
    self,
    job_id: str,
    audio_path: str,
    do_separate: bool = True,          # NOTE: NOT named 'separate_stems' — avoids
                                        # shadowing the imported function above
    instruments: list[str] | None = None,
    output_format: str = "musicxml",
    quantize: bool = True,
    onset_threshold: float = 0.5,
    frame_threshold: float = 0.3,
    min_note_length_ms: int = 58,
) -> dict:
    """
    Full transcription pipeline as a Celery task.

    Parameters match the kwargs passed by main.py's transcribe_async endpoint.
    Returns the completed result dict (also stored in Celery's Redis backend).
    """
    instruments = instruments or []
    job_dir = settings.output_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # ── Stage scaffolding ─────────────────────────────────────────────────────
    stages: list[dict[str, Any]] = [
        {"id": "separation",    "label": "Source Separation (Demucs)",       "state": "pending", "progress": 0.0},
        {"id": "transcription", "label": "Pitch Transcription (Basic Pitch)", "state": "pending", "progress": 0.0},
        {"id": "instrument_id", "label": "Instrument Detection",              "state": "pending", "progress": 0.0},
        {"id": "notation",      "label": "Sheet Music Generation",            "state": "pending", "progress": 0.0},
    ]

    def set_stage(idx: int, state: str, progress: float = 0.0) -> None:
        stages[idx]["state"] = state
        stages[idx]["progress"] = progress

    stem_infos: list[dict] = []

    try:
        # ══════════════════════════════════════════════════════════════════════
        # STAGE 1 — Source Separation
        # ══════════════════════════════════════════════════════════════════════
        set_stage(0, "running")
        _push(self, "processing", 0.05, stages)

        if do_separate:
            stem_paths = separate_stems(
                audio_path=audio_path,
                output_dir=str(job_dir),
                model=settings.demucs_model,
            )
        else:
            # Single-instrument or already-separated file: treat as one stem.
            stem_paths = [{"id": "other_0", "label": "Full Mix", "path": audio_path}]

        set_stage(0, "complete", 1.0)

        # Build the stem info list that Flutter shows as playable cards.
        # audio_url points to FastAPI's /outputs static mount.
        stem_infos = [
            {
                "id":        s["id"],
                "label":     s["label"],
                "audio_url": settings.output_url(job_id, Path(s["path"]).name),
            }
            for s in stem_paths
        ]
        _push(self, "processing", 0.30, stages, stems=stem_infos)

        # ══════════════════════════════════════════════════════════════════════
        # STAGE 2 — Pitch Transcription (Basic Pitch, per stem)
        # ══════════════════════════════════════════════════════════════════════
        set_stage(1, "running")
        _push(self, "processing", 0.35, stages, stems=stem_infos)

        midi_results: list[dict] = []
        n_stems = len(stem_paths)

        for i, stem in enumerate(stem_paths):
            midi_path, note_events = transcribe_stem(
                audio_path=stem["path"],
                output_dir=str(job_dir),
                stem_id=stem["id"],
                quantize=quantize,
                onset_threshold=onset_threshold,
                frame_threshold=frame_threshold,
                min_note_length_ms=min_note_length_ms,
            )
            midi_results.append({
                "stem_id":     stem["id"],
                "label":       stem["label"],
                "midi_path":   midi_path,
                "note_events": note_events,
            })

            stem_progress = (i + 1) / n_stems
            set_stage(1, "running", stem_progress)
            _push(self, "processing", 0.35 + 0.25 * stem_progress,
                  stages, stems=stem_infos)

        set_stage(1, "complete", 1.0)

        # ══════════════════════════════════════════════════════════════════════
        # STAGE 3 — Instrument Detection
        # ══════════════════════════════════════════════════════════════════════
        set_stage(2, "running")
        _push(self, "processing", 0.62, stages, stems=stem_infos)

        detected_instruments: list[dict] = []
        for stem in stem_paths:
            inst = classify_instrument(
                audio_path=stem["path"],
                hint_label=stem["label"],  # Demucs label provides a strong prior
            )
            detected_instruments.append({
                "stem_id":    stem["id"],
                "stem_label": stem["label"],
                **inst,        # keys: name, confidence, emoji
            })

        set_stage(2, "complete", 1.0)
        _push(self, "processing", 0.70, stages, stems=stem_infos)

        # ══════════════════════════════════════════════════════════════════════
        # STAGE 4 — Score Generation (music21 → MusicXML + PDF + MIDI)
        # ══════════════════════════════════════════════════════════════════════
        set_stage(3, "running")
        _push(self, "processing", 0.75, stages, stems=stem_infos)

        score_paths = build_score(
            midi_results=midi_results,
            detected_instruments=detected_instruments,
            output_dir=str(job_dir),
            output_format=output_format,
            quantize=quantize,
        )

        set_stage(3, "complete", 1.0)

        # ── Assemble final result ──────────────────────────────────────────────
        inst_list = []
        for inst in detected_instruments:
            midi_r = next(
                (m for m in midi_results if m["stem_id"] == inst["stem_id"]), {}
            )
            inst_list.append({
                "name":        inst.get("name", inst["stem_label"]),
                "stem_label":  inst["stem_label"],
                "confidence":  inst.get("confidence", 1.0),
                "note_count":  len(midi_r.get("note_events", [])),
                "emoji":       inst.get("emoji", "🎵"),
            })

        result: dict[str, Any] = {
            "musicxml_url":         settings.output_url(job_id, score_paths["musicxml"]),
            "pdf_url":              settings.output_url(job_id, score_paths["pdf"]),
            "midi_url":             settings.output_url(job_id, score_paths["midi"]),
            "detected_instruments": inst_list,
            # Include stage snapshot and stems in SUCCESS result so
            # GET /jobs/{id} can reconstruct the full response.
            "stages":               stages,
            "stems":                stem_infos,
        }

        _push(self, "complete", 1.0, stages, stems=stem_infos, result=result)
        return result

    except Exception as exc:
        traceback.print_exc()
        error_msg = f"{type(exc).__name__}: {exc}"

        # Mark whichever stage was running as failed
        for s in stages:
            if s["state"] == "running":
                s["state"] = "failed"

        _push(self, "failed", 0.0, stages, stems=stem_infos or None, error=error_msg)

        # Re-raise so Celery marks the task FAILURE and stores the exception
        raise