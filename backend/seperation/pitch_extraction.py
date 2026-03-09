"""
basic_pitch_runner.py
═════════════════════
Wraps Spotify's Basic Pitch for per-stem pitch transcription.

Called from two places:
  1. workers/tasks.py   — async Celery pipeline (one call per Demucs stem)
  2. main.py            — synchronous /transcribe/direct endpoint

The entry point is transcribe_stem(audio_path, output_dir, stem_id, ...)
which accepts the absolute file path written by FastAPI's _save_upload(),
runs Basic Pitch, and returns:
  - midi_path   : str  — absolute path to the written .mid file
  - note_events : list — JSON-serialisable list of note dicts

Install
───────
  pip install basic-pitch librosa pretty_midi

Basic Pitch paper: https://arxiv.org/abs/2203.13128
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np


# ── Public entry point ─────────────────────────────────────────────────────────

def transcribe_stem(
    audio_path: str,
    output_dir: str,
    stem_id: str = "other",
    quantize: bool = True,
    onset_threshold: float = 0.5,
    frame_threshold: float = 0.3,
    min_note_length_ms: int = 58,
) -> tuple[str, list[dict[str, Any]]]:
    """
    Transcribe one audio stem to MIDI using Basic Pitch.

    Parameters
    ──────────
    audio_path        Absolute path to the audio file (WAV/MP3/FLAC etc.)
                      This is the path written by FastAPI's _save_upload().
    output_dir        Directory to write the .mid file into.
    stem_id           Demucs stem name or 'other' for full-mix / unknown.
                      Drums are routed to a dedicated onset-based transcriber.
    quantize          If True, snap note onsets/durations to a 16th-note grid
                      in post-processing (music21 does this again later, but
                      having clean MIDI helps).
    onset_threshold   Basic Pitch onset sensitivity (0–1).
                      Lower → more notes detected (risk: more false positives).
                      Higher → only high-confidence onsets.
    frame_threshold   Basic Pitch frame sensitivity (0–1).
                      Controls how long sustained notes are detected.
    min_note_length_ms  Notes shorter than this (ms) are discarded.

    Returns
    ───────
    (midi_path, note_events)
      midi_path    : str  — absolute path to written MIDI file
      note_events  : list of dicts, each with keys:
                       start      (float, seconds)
                       end        (float, seconds)
                       pitch      (int, MIDI note number 0–127)
                       velocity   (int, 0–127, from amplitude estimation)
                       confidence (float, 0–1)
    """
    audio_path = Path(audio_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    # Route drums to onset-based transcription (Basic Pitch is for pitched audio)
    if stem_id == "drums":
        return _transcribe_drums(audio_path, output_dir)

    return _transcribe_pitched(
        audio_path=audio_path,
        output_dir=output_dir,
        stem_id=stem_id,
        onset_threshold=onset_threshold,
        frame_threshold=frame_threshold,
        min_note_length_ms=min_note_length_ms,
        quantize=quantize,
    )


# ── Pitched instruments via Basic Pitch ───────────────────────────────────────

def _transcribe_pitched(
    audio_path: Path,
    output_dir: Path,
    stem_id: str,
    onset_threshold: float,
    frame_threshold: float,
    min_note_length_ms: int,
    quantize: bool,
) -> tuple[str, list[dict[str, Any]]]:
    from basic_pitch.inference import predict
    from basic_pitch import ICASSP_2022_MODEL_PATH

    # ── Run Basic Pitch inference ─────────────────────────────────────────────
    # predict() returns three objects:
    #   model_output : dict of raw tensors (onsets, frames, contours)
    #   midi_data    : pretty_midi.PrettyMIDI — ready to write to disk
    #   note_events  : np.ndarray of shape (N, 5):
    #                  [start_time, end_time, pitch_midi, amplitude, pitch_bend_midi]
    #
    # melodia_trick=False is intentional: it improves single-melody extraction
    # but actively hurts chord detection by suppressing weaker simultaneous notes.
    # For a general-purpose transcriber we want chords, so we leave it off.

    model_output, midi_data, note_events = predict(
        str(audio_path),
        onset_threshold=onset_threshold,
        frame_threshold=frame_threshold,
        minimum_note_length=min_note_length_ms,
        minimum_frequency=27.5,   # A0 — lowest piano note; safe floor for all instruments
        maximum_frequency=4186.0, # C8 — highest piano note
        multiple_pitch_bends=False,
        melodia_trick=False,      # OFF: preserves chords (see note above)
    )

    # ── Optionally snap to 16th-note grid ────────────────────────────────────
    if quantize:
        midi_data = _quantize_midi(midi_data)

    # ── Write MIDI to disk ────────────────────────────────────────────────────
    midi_path = output_dir / f"{stem_id}.mid"
    midi_data.write(str(midi_path))

    # ── Build JSON-serialisable note event list ───────────────────────────────
    # note_events rows: [start, end, pitch, amplitude, pitch_bend]
    # We map amplitude (0–1 float) → velocity (0–127 int) using sqrt curve
    # which better matches perceived loudness (see: MIDI velocity perception).
    events = []
    for row in note_events:
        start, end, pitch, amplitude = float(row[0]), float(row[1]), int(row[2]), float(row[3])
        velocity = _amplitude_to_velocity(amplitude)
        events.append({
            "start":      round(start, 4),
            "end":        round(end, 4),
            "pitch":      pitch,
            "velocity":   velocity,
            "confidence": round(float(amplitude), 4),  # amplitude serves as per-note confidence
        })

    return str(midi_path), events


# ── Drum transcription (onset-based, bypasses Basic Pitch) ───────────────────

def _transcribe_drums(
    audio_path: Path,
    output_dir: Path,
) -> tuple[str, list[dict[str, Any]]]:
    """
    Simple onset-based drum transcription.
    Maps onsets to kick (36), snare (38), or hi-hat (42) via spectral centroid.

    TODO: replace with ADTLib or a proper drum transcription model for
    accurate multi-drum-pad detection.
    """
    import librosa
    import pretty_midi

    y, sr = librosa.load(str(audio_path), sr=22050, mono=True)

    # Onset detection with percussive source separation for cleaner onsets
    y_perc = librosa.effects.percussive(y, margin=3)
    onset_frames = librosa.onset.onset_detect(
        y=y_perc, sr=sr, units="frames",
        pre_max=1, post_max=1, pre_avg=3, post_avg=3, delta=0.07, wait=10,
    )
    onset_times = librosa.frames_to_time(onset_frames, sr=sr)
    spectral_centroids = librosa.feature.spectral_centroid(y=y, sr=sr)[0]

    pm = pretty_midi.PrettyMIDI()
    drum_track = pretty_midi.Instrument(program=0, is_drum=True, name="Drums")
    note_events: list[dict[str, Any]] = []

    for t in onset_times:
        frame_idx = librosa.time_to_frames(t, sr=sr)
        frame_idx = int(min(frame_idx, len(spectral_centroids) - 1))
        centroid = float(spectral_centroids[frame_idx])

        # Heuristic pitch assignment by spectral brightness
        if centroid < 400:
            pitch, label = 36, "kick"
        elif centroid < 2500:
            pitch, label = 38, "snare"
        else:
            pitch, label = 42, "hihat"

        note_end = float(t) + 0.05
        drum_track.notes.append(
            pretty_midi.Note(velocity=90, pitch=pitch, start=float(t), end=note_end)
        )
        note_events.append({
            "start": round(float(t), 4),
            "end": round(note_end, 4),
            "pitch": pitch,
            "velocity": 90,
            "confidence": 1.0,
            "label": label,
        })

    pm.instruments.append(drum_track)
    midi_path = output_dir / "drums.mid"
    pm.write(str(midi_path))

    return str(midi_path), note_events


# ── Helpers ───────────────────────────────────────────────────────────────────

def _amplitude_to_velocity(amplitude: float) -> int:
    """
    Map Basic Pitch amplitude (0–1) → MIDI velocity (1–127).

    Uses sqrt curve: v = round(127 * sqrt(amplitude))
    This matches perceived loudness better than a linear mapping because
    MIDI synthesisers typically interpret velocity on a roughly quadratic curve.
    Clamp to 1 minimum so every note has an audible velocity.
    """
    velocity = round(127 * math.sqrt(max(0.0, min(1.0, amplitude))))
    return max(1, min(127, velocity))


def _quantize_midi(midi_data) -> object:
    """
    Snap note start/end times to a 16th-note grid (0.25 quarter notes).
    Operates on the PrettyMIDI object returned by Basic Pitch.

    The full score quantization happens again in score_builder.py via music21;
    this pass just cleans up the MIDI so that very close simultaneous notes
    (e.g. a chord where fingers landed 5ms apart) snap together cleanly.
    """
    import pretty_midi

    # Estimate tempo from the MIDI (Basic Pitch defaults to 120 BPM)
    tempo_change_times, tempos = midi_data.get_tempo_changes()
    bpm = float(tempos[0]) if len(tempos) > 0 else 120.0
    seconds_per_beat = 60.0 / bpm
    grid = seconds_per_beat / 4  # 16th note

    for instrument in midi_data.instruments:
        for note in instrument.notes:
            note.start = round(note.start / grid) * grid
            note.end   = max(note.start + grid, round(note.end / grid) * grid)

    return midi_data