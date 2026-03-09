"""
pipeline/separation/demucs_separator.py
────────────────────────────────────────
Wraps Meta's Demucs for audio source separation.

    pip install demucs

Demucs models
─────────────
htdemucs      4-stem: drums / bass / vocals / other   (default, fast)
htdemucs_6s   6-stem: adds guitar + piano             (slower, more detail)
mdx_extra     4-stem MDX variant, sometimes better on music

Output layout written by Demucs CLI
────────────────────────────────────
<output_dir>/<model_name>/<track_stem>/
    drums.wav  bass.wav  vocals.wav  other.wav
    (or .mp3 if --mp3 is passed)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any


# Human-readable label + emoji per Demucs output stem name
STEM_META: dict[str, dict[str, str]] = {
    "drums":  {"label": "Drums",   "emoji": "🥁"},
    "bass":   {"label": "Bass",    "emoji": "🎸"},
    "vocals": {"label": "Vocals",  "emoji": "🎤"},
    "guitar": {"label": "Guitar",  "emoji": "🎸"},
    "piano":  {"label": "Piano",   "emoji": "🎹"},
    "other":  {"label": "Other",   "emoji": "🎵"},
}


def separate_stems(
    audio_path: str,
    output_dir: str,
    model: str = "htdemucs",
    mp3_output: bool = True,
) -> list[dict[str, Any]]:
    """
    Run Demucs on *audio_path* and return a list of stem dicts:

        [
            {"id": "drums",  "label": "Drums",  "emoji": "🥁", "path": "/tmp/.../drums.mp3"},
            {"id": "bass",   "label": "Bass",   "emoji": "🎸", "path": "/tmp/.../bass.mp3"},
            ...
        ]

    Parameters
    ──────────
    audio_path   Absolute path to source audio (WAV/MP3/FLAC etc.)
    output_dir   Root directory for Demucs output. Demucs creates:
                     <output_dir>/<model>/<track_name>/<stem>.{wav,mp3}
    model        Demucs model name (see module docstring).
    mp3_output   If True, request MP3 output (smaller files for stem preview).
                 Set False for lossless WAV (better for transcription quality).

    Raises
    ──────
    subprocess.CalledProcessError  if Demucs exits non-zero
    RuntimeError                   if no stem files are found after running
    """
    audio_path = Path(audio_path)
    output_dir = Path(output_dir)

    cmd = [
        sys.executable, "-m", "demucs",
        "--name", model,
        "--out", str(output_dir),
    ]
    if mp3_output:
        cmd.append("--mp3")
    cmd.append(str(audio_path))

    subprocess.run(cmd, capture_output=True, text=True, check=True)

    # Demucs writes to: <output_dir>/<model>/<audio_stem>/
    track_name = audio_path.stem
    stem_dir = output_dir / model / track_name

    stems: list[dict[str, Any]] = []
    for stem_path in sorted(stem_dir.glob("*.wav")) + sorted(stem_dir.glob("*.mp3")):
        stem_id = stem_path.stem          # e.g. "drums"
        meta = STEM_META.get(stem_id, {"label": stem_id.capitalize(), "emoji": "🎵"})
        stems.append({
            "id":    stem_id,
            "label": meta["label"],
            "emoji": meta["emoji"],
            "path":  str(stem_path),
        })

    if not stems:
        raise RuntimeError(
            f"Demucs produced no stem files in {stem_dir}. "
            "Check that Demucs completed successfully."
        )

    return stems