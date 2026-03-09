"""
pipeline/notation/score_builder.py
────────────────────────────────────
Merges per-stem MIDI files into a single notated score.

Steps
─────
1. Load each stem MIDI as a music21 Part
2. Assign the detected instrument to the part
3. Quantise note durations to a rhythmic grid
4. Auto-detect key signature from combined pitch content
5. Insert time signature (4/4 default; TODO: detect from audio)
6. Export → MusicXML, combined MIDI, and optionally PDF

PDF export
──────────
Attempts MuseScore CLI first (mscore3 / mscore), then falls back to
LilyPond if available. If neither is installed, the PDF URL in the
response falls back to the MusicXML URL — the Flutter OSMD WebView
renders MusicXML natively so no PDF is strictly required.

    Install MuseScore:   apt install musescore3   /   brew install musescore
    Install LilyPond:    apt install lilypond     /   brew install lilypond
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from music21 import converter, instrument, meter, stream, tempo


# ── Instrument name → music21 class ───────────────────────────────────────────

INSTRUMENT_MAP: dict[str, type] = {
    "Piano":             instrument.Piano,
    "Electric Piano":    instrument.ElectricPiano,
    "Organ":             instrument.PipeOrgan,
    "Guitar":            instrument.Guitar,
    "Electric Guitar":   instrument.ElectricGuitar,
    "Bass Guitar":       instrument.ElectricBass,
    "Violin":            instrument.Violin,
    "Viola":             instrument.Viola,
    "Cello":             instrument.Violoncello,
    "Trumpet":           instrument.Trumpet,
    "Saxophone":         instrument.Saxophone,
    "Alto Saxophone":    instrument.AltoSaxophone,
    "Flute":             instrument.Flute,
    "Clarinet":          instrument.Clarinet,
    "Voice":             instrument.Vocalist,
    "Drum Kit":          instrument.UnpitchedPercussion,
}


# ── Public API ─────────────────────────────────────────────────────────────────

def build_score(
    midi_results: list[dict[str, Any]],
    detected_instruments: list[dict[str, Any]],
    output_dir: str,
    output_format: str = "musicxml",
    quantize: bool = True,
    quantize_quarter_length: float = 0.25,   # 16th-note grid
    default_bpm: int = 120,
) -> dict[str, str]:
    """
    Merge per-stem MIDI files into a notated score and export.

    Parameters
    ──────────
    midi_results          List of dicts from basic_pitch_runner:
                              {stem_id, label, midi_path, note_events}
    detected_instruments  List of dicts from instrument_classifier:
                              {stem_id, stem_label, name, confidence, emoji}
    output_dir            Directory to write score files into.
    output_format         Primary format: 'musicxml' | 'midi' | 'pdf'
    quantize              Snap note offsets/durations to grid.
    quantize_quarter_length  Grid size in quarter notes (0.25 = 16th note).
    default_bpm           Fallback tempo if none detected.

    Returns
    ───────
    dict mapping format → filename (not full path):
        {"musicxml": "score.musicxml", "midi": "score.mid", "pdf": "score.pdf"}
    The PDF value falls back to "score.musicxml" if no renderer is available.
    """
    output_dir = Path(output_dir)
    score = stream.Score()

    inst_by_stem = {d["stem_id"]: d for d in detected_instruments}

    for midi_result in midi_results:
        stem_id   = midi_result["stem_id"]
        midi_path = midi_result["midi_path"]
        inst_info = inst_by_stem.get(stem_id, {})
        inst_name = inst_info.get("name", "Piano")

        part = _load_midi_as_part(midi_path, inst_name)

        if quantize:
            part = _quantize_part(part, quarter_length=quantize_quarter_length)

        score.append(part)

    # ── Score-level annotations ────────────────────────────────────────────────
    detected_key = score.analyze("key")

    for part in score.parts:
        # Insert at offset 0 so these appear before any notes
        part.insert(0, detected_key)
        part.insert(0, meter.TimeSignature("4/4"))
        part.insert(0, tempo.MetronomeMark(number=default_bpm))

    # ── Export ─────────────────────────────────────────────────────────────────
    outputs: dict[str, str] = {}

    xml_path = output_dir / "score.musicxml"
    score.write("musicxml", str(xml_path))
    outputs["musicxml"] = xml_path.name

    midi_path_out = output_dir / "score.mid"
    score.write("midi", str(midi_path_out))
    outputs["midi"] = midi_path_out.name

    pdf_result = _export_pdf(xml_path, output_dir)
    outputs["pdf"] = pdf_result.name if pdf_result else xml_path.name

    return outputs


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_midi_as_part(midi_path: str, instrument_name: str) -> stream.Part:
    """Parse a MIDI file and return a music21 Part with the correct instrument."""
    parsed = converter.parse(midi_path)

    if isinstance(parsed, stream.Score) and parsed.parts:
        part = parsed.parts[0]
    else:
        part = parsed.flatten().makeMeasures()

    inst_class = INSTRUMENT_MAP.get(instrument_name, instrument.Piano)
    part.insert(0, inst_class())
    part.partName = instrument_name
    return part


def _quantize_part(part: stream.Part, quarter_length: float = 0.25) -> stream.Part:
    """Snap note offsets and durations to the nearest rhythmic grid."""
    divisor = int(round(1.0 / quarter_length))   # e.g. 4 for 16th-note grid
    return part.quantize(
        quarterLengthDivisors=(divisor,),
        processOffsets=True,
        processDurations=True,
        inPlace=False,
    )


def _export_pdf(xml_path: Path, output_dir: Path) -> Path | None:
    """
    Export the MusicXML file to PDF using MuseScore or LilyPond CLI.
    Returns the PDF Path on success, or None if no renderer is available.
    """
    pdf_path = output_dir / "score.pdf"

    # ── MuseScore (preferred) ──────────────────────────────────────────────
    mscore = shutil.which("mscore3") or shutil.which("mscore") or shutil.which("musescore")
    if mscore:
        try:
            subprocess.run(
                [mscore, "--export-to", str(pdf_path), str(xml_path)],
                capture_output=True,
                timeout=90,
                check=True,
            )
            if pdf_path.exists():
                return pdf_path
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass

    # ── LilyPond fallback ──────────────────────────────────────────────────
    if shutil.which("lilypond"):
        try:
            # music21 can write LilyPond source; pipe through lilypond
            from music21 import converter as m21conv
            score = m21conv.parse(str(xml_path))
            score.write("lily.pdf", str(pdf_path))
            if pdf_path.exists():
                return pdf_path
        except Exception:
            pass

    return None