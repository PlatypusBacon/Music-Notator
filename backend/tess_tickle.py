# test_pipeline.py  — run from your project root
# python test_pipeline.py path/to/your/audio.wav

import sys
from pathlib import Path
from seperation.pitch_extraction import transcribe_stem

def test_file(audio_path: str):
    output_dir = Path("/tmp/scorescribe/test_output")
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Processing: {audio_path}")
    print(f"Output dir: {output_dir}")

    midi_path, note_events = transcribe_stem(
        audio_path=audio_path,
        output_dir=str(output_dir),
        stem_id="other",
        quantize=True,
        onset_threshold=0.5,
        frame_threshold=0.3,
        min_note_length_ms=58,
    )

    print(f"\n✓ MIDI written to: {midi_path}")
    print(f"✓ Note count: {len(note_events)}")
    print(f"\nFirst 5 notes:")
    for note in note_events[:5]:
        print(f"  {note}")

if __name__ == "__main__":
    audio = sys.argv[1] if len(sys.argv) > 1 else "test_audio.wav"
    test_file(audio)