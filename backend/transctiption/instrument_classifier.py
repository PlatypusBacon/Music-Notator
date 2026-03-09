"""
pipeline/transcription/instrument_classifier.py
────────────────────────────────────────────────
Classifies the dominant instrument in a separated audio stem.

Two-stage strategy
──────────────────
1. Stem-label prior — Demucs stem names already tell us a lot:
       drums  → Drum Kit    (confidence 0.92)
       bass   → Bass Guitar (confidence 0.85)
       vocals → Voice       (confidence 0.90)
       guitar → Guitar      (confidence 0.87)
       piano  → Piano       (confidence 0.88)
   These priors are used directly when their confidence is >= 0.85,
   skipping the more expensive model inference.

2. YAMNet (Google) — for the "other" stem and any low-confidence priors,
   YAMNet embeddings are extracted and the top audio class is mapped to
   an instrument name. YAMNet is loaded once and cached between calls.

   To install:  pip install tensorflow tensorflow-hub

   YAMNet class map CSV is fetched automatically by tensorflow-hub.
   For production: download yamnet_class_map.csv and serve locally.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import numpy as np


# ── Stem-label priors ──────────────────────────────────────────────────────────

STEM_PRIORS: dict[str, dict[str, Any]] = {
    "drums":  {"name": "Drum Kit",    "confidence": 0.92, "emoji": "🥁"},
    "bass":   {"name": "Bass Guitar", "confidence": 0.85, "emoji": "🎸"},
    "vocals": {"name": "Voice",       "confidence": 0.90, "emoji": "🎤"},
    "guitar": {"name": "Guitar",      "confidence": 0.87, "emoji": "🎸"},
    "piano":  {"name": "Piano",       "confidence": 0.88, "emoji": "🎹"},
}

# Subset of YAMNet class indices that map to musical instruments.
# Source: https://github.com/tensorflow/models/blob/master/research/audioset/yamnet/yamnet_class_map.csv
YAMNET_INSTRUMENT_MAP: dict[int, tuple[str, str]] = {
    137: ("Piano",           "🎹"),
    138: ("Organ",           "🎹"),
    139: ("Electric Piano",  "🎹"),
    140: ("Guitar",          "🎸"),
    141: ("Electric Guitar", "🎸"),
    142: ("Bass Guitar",     "🎸"),
    117: ("Violin",          "🎻"),
    118: ("Cello",           "🎻"),
    119: ("Viola",           "🎻"),
    107: ("Trumpet",         "🎺"),
    108: ("Saxophone",       "🎷"),
    109: ("Alto Saxophone",  "🎷"),
    114: ("Flute",           "🪈"),
    115: ("Clarinet",        "🎶"),
    66:  ("Drum Kit",        "🥁"),
    70:  ("Bass Drum",       "🥁"),
    73:  ("Snare Drum",      "🥁"),
    74:  ("Hi-hat",          "🥁"),
    75:  ("Cymbal",          "🥁"),
}


# ── Public API ─────────────────────────────────────────────────────────────────

def classify_instrument(
    audio_path: str,
    hint_label: str = "",
    use_model: bool = True,
) -> dict[str, Any]:
    """
    Classify the dominant instrument in *audio_path*.

    Parameters
    ──────────
    audio_path   Absolute path to a separated stem (WAV/MP3).
    hint_label   Demucs stem name, e.g. "drums", "bass", "other".
                 Normalised to lowercase with spaces→underscores.
    use_model    If True, fall back to YAMNet when the prior is absent or
                 has low confidence. Set False to use only priors (faster).

    Returns
    ───────
    dict with keys: name (str), confidence (float 0–1), emoji (str)
    """
    stem_key = hint_label.lower().replace(" ", "_").rstrip("_0123456789")

    prior = STEM_PRIORS.get(stem_key)

    # High-confidence known stem — use prior directly
    if prior and (prior["confidence"] >= 0.85 or not use_model):
        return dict(prior)

    # Run YAMNet
    model_result = _yamnet_classify(audio_path)

    if prior:
        # Return whichever is more confident
        return model_result if model_result["confidence"] > prior["confidence"] else dict(prior)

    return model_result


# ── YAMNet inference (lazy-loaded, module-level cache) ────────────────────────

@functools.lru_cache(maxsize=1)
def _load_yamnet():
    """Load YAMNet from TensorFlow Hub. Cached — loaded only once per worker."""
    import tensorflow_hub as hub
    return hub.load("https://tfhub.dev/google/yamnet/1")


def _yamnet_classify(audio_path: str) -> dict[str, Any]:
    """Run YAMNet on audio_path and return the best instrument match."""
    try:
        import tensorflow as tf
        import librosa

        yamnet = _load_yamnet()

        y, sr = librosa.load(audio_path, sr=16000, mono=True)
        waveform = tf.constant(y, dtype=tf.float32)
        scores, _embeddings, _log_mel = yamnet(waveform)

        # Average class scores across all time frames
        mean_scores = tf.reduce_mean(scores, axis=0).numpy()

        # Find best instrument class (restrict to known instrument indices)
        best_idx: int | None = None
        best_score = -1.0
        for idx in YAMNET_INSTRUMENT_MAP:
            if idx < len(mean_scores) and mean_scores[idx] > best_score:
                best_score = float(mean_scores[idx])
                best_idx = idx

        if best_idx is not None and best_score > 0.0:
            name, emoji = YAMNET_INSTRUMENT_MAP[best_idx]
            return {"name": name, "confidence": round(best_score, 4), "emoji": emoji}

    except Exception:
        pass  # TF not installed, network error, etc.

    return {"name": "Unknown", "confidence": 0.0, "emoji": "🎵"}