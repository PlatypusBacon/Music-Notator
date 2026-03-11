"""
workers/tasks.py
────────────────
Celery task: orchestrates the four-stage transcription pipeline.
Every stage emits DEBUG logs with timing so you can see exactly
where time is being spent in the Celery worker log.
"""

from __future__ import annotations

import logging
import time
import traceback
from pathlib import Path
from typing import Any

from workers.celery_app import celery_app
from seperation.demucs_seperator import separate_stems
from seperation.pitch_extraction import transcribe_stem
from transcription.instrument_classifier import classify_instrument
from notation.score_builder import build_score
from config import settings
from bedug import setup_logging

setup_logging(level="DEBUG", log_file=Path(__file__).parent.parent / "celery_pipeline.log")
log = logging.getLogger("scorescribe.task")


# ── Progress helper ────────────────────────────────────────────────────────────

def _push(task, state: str, progress: float, stages: list[dict],
          stems: list[dict] | None = None,
          result: dict | None = None,
          error: str | None = None) -> None:
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
    max_retries=0,
    soft_time_limit=600,
    time_limit=660,
)
def run_transcription_pipeline(
    self,
    job_id: str,
    audio_path: str,
    do_separate: bool = True,
    instruments: list[str] | None = None,
    output_format: str = "musicxml",
    quantize: bool = True,
    onset_threshold: float = 0.5,
    frame_threshold: float = 0.3,
    min_note_length_ms: int = 58,
) -> dict:
    instruments = instruments or []
    job_dir = settings.output_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    task_start = time.perf_counter()
    log.info("═" * 55)
    log.info("JOB %s  pipeline starting", job_id)
    log.info("  audio       : %s", audio_path)
    log.info("  do_separate : %s", do_separate)
    log.info("  quantize    : %s", quantize)
    log.info("  onset_thr   : %s", onset_threshold)
    log.info("  frame_thr   : %s", frame_threshold)
    log.info("  min_note_ms : %s", min_note_length_ms)
    log.info("  output_dir  : %s", job_dir)
    log.info("═" * 55)

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
        t1 = time.perf_counter()
        set_stage(0, "running")
        _push(self, "processing", 0.05, stages)
        log.info("JOB %s  ── STAGE 1: source separation  do_separate=%s", job_id, do_separate)

        if do_separate:
            log.debug("JOB %s  running Demucs model=%s", job_id, settings.demucs_model)
            stem_paths = separate_stems(
                audio_path=audio_path,
                output_dir=str(job_dir),
                model=settings.demucs_model,
            )
            log.info("JOB %s  ── separation done  stems=%s  %.1fs",
                     job_id, [s["id"] for s in stem_paths], time.perf_counter() - t1)
        else:
            stem_paths = [{"id": "other_0", "label": "Full Mix", "path": audio_path}]
            log.info("JOB %s  ── separation skipped (single stem)", job_id)

        set_stage(0, "complete", 1.0)

        stem_infos = [
            {
                "id":        s["id"],
                "label":     s["label"],
                "audio_url": settings.output_url(job_id, Path(s["path"]).name),
            }
            for s in stem_paths
        ]
        log.debug("JOB %s  stem_infos=%s", job_id, [s["id"] for s in stem_infos])
        _push(self, "processing", 0.30, stages, stems=stem_infos)

        # ══════════════════════════════════════════════════════════════════════
        # STAGE 2 — Pitch Transcription
        # ══════════════════════════════════════════════════════════════════════
        t2 = time.perf_counter()
        set_stage(1, "running")
        _push(self, "processing", 0.35, stages, stems=stem_infos)
        log.info("JOB %s  ── STAGE 2: pitch transcription  stems=%d", job_id, len(stem_paths))

        midi_results: list[dict] = []
        n_stems = len(stem_paths)

        for i, stem in enumerate(stem_paths):
            st = time.perf_counter()
            log.debug("JOB %s  transcribing stem %d/%d: %s  path=%s",
                      job_id, i + 1, n_stems, stem["id"], stem["path"])

            midi_path, note_events = transcribe_stem(
                audio_path=stem["path"],
                output_dir=str(job_dir),
                stem_id=stem["id"],
                quantize=quantize,
                onset_threshold=onset_threshold,
                frame_threshold=frame_threshold,
                min_note_length_ms=min_note_length_ms,
            )
            elapsed_stem = time.perf_counter() - st
            log.info("JOB %s  ── stem '%s' → %d notes  midi=%s  %.1fs",
                     job_id, stem["id"], len(note_events), midi_path, elapsed_stem)

            midi_results.append({
                "stem_id":     stem["id"],
                "label":       stem["label"],
                "midi_path":   midi_path,
                "note_events": note_events,
            })

            stem_progress = (i + 1) / n_stems
            set_stage(1, "running", stem_progress)
            _push(self, "processing", 0.35 + 0.25 * stem_progress, stages, stems=stem_infos)

        set_stage(1, "complete", 1.0)
        log.info("JOB %s  ── transcription done  total_notes=%d  %.1fs",
                 job_id, sum(len(m["note_events"]) for m in midi_results),
                 time.perf_counter() - t2)

        # ══════════════════════════════════════════════════════════════════════
        # STAGE 3 — Instrument Detection
        # ══════════════════════════════════════════════════════════════════════
        t3 = time.perf_counter()
        set_stage(2, "running")
        _push(self, "processing", 0.62, stages, stems=stem_infos)
        log.info("JOB %s  ── STAGE 3: instrument detection", job_id)

        detected_instruments: list[dict] = []
        for stem in stem_paths:
            log.debug("JOB %s  classifying stem '%s'  hint='%s'",
                      job_id, stem["id"], stem["label"])
            inst = classify_instrument(
                audio_path=stem["path"],
                hint_label=stem["label"],
            )
            log.info("JOB %s  ── '%s' → %s  confidence=%.2f",
                     job_id, stem["id"], inst.get("name"), inst.get("confidence"))
            detected_instruments.append({
                "stem_id":    stem["id"],
                "stem_label": stem["label"],
                **inst,
            })

        set_stage(2, "complete", 1.0)
        _push(self, "processing", 0.70, stages, stems=stem_infos)
        log.info("JOB %s  ── instrument detection done  %.1fs",
                 job_id, time.perf_counter() - t3)

        # ══════════════════════════════════════════════════════════════════════
        # STAGE 4 — Score Generation
        # ══════════════════════════════════════════════════════════════════════
        t4 = time.perf_counter()
        set_stage(3, "running")
        _push(self, "processing", 0.75, stages, stems=stem_infos)
        log.info("JOB %s  ── STAGE 4: score generation  format=%s  quantize=%s",
                 job_id, output_format, quantize)

        score_paths = build_score(
            midi_results=midi_results,
            detected_instruments=detected_instruments,
            output_dir=str(job_dir),
            output_format=output_format,
            quantize=quantize,
        )
        log.info("JOB %s  ── score written  files=%s  %.1fs",
                 job_id, score_paths, time.perf_counter() - t4)

        set_stage(3, "complete", 1.0)

        # ── Final result ───────────────────────────────────────────────────────
        inst_list = []
        for inst in detected_instruments:
            midi_r = next((m for m in midi_results if m["stem_id"] == inst["stem_id"]), {})
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
            "stages":               stages,
            "stems":                stem_infos,
        }

        total = time.perf_counter() - task_start
        log.info("JOB %s  ══ PIPELINE COMPLETE  total=%.1fs ══", job_id, total)
        log.info("  musicxml : %s", result["musicxml_url"])
        log.info("  midi     : %s", result["midi_url"])
        log.info("  pdf      : %s", result["pdf_url"])

        _push(self, "complete", 1.0, stages, stems=stem_infos, result=result)
        return result

    except Exception as exc:
        elapsed = time.perf_counter() - task_start
        error_msg = f"{type(exc).__name__}: {exc}"
        log.error("JOB %s  ══ PIPELINE FAILED  %.1fs  error=%s", job_id, elapsed, error_msg)
        log.debug("JOB %s  traceback:\n%s", job_id, traceback.format_exc())

        for s in stages:
            if s["state"] == "running":
                s["state"] = "failed"

        _push(self, "failed", 0.0, stages, stems=stem_infos or None, error=error_msg)
        raise