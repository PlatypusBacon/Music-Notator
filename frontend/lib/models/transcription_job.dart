// =============================================================================
// transcription_job.dart
// All data models for the ScoreScribe transcription pipeline.
// Mirrors the Pydantic schemas in backend/api/schemas.py exactly.
// =============================================================================

// ── TranscriptionJob ──────────────────────────────────────────────────────────
// Returned immediately when audio is POSTed to /api/v1/transcribe.

class TranscriptionJob {
  final String id;
  final DateTime createdAt;
  final String? originalFilename;

  const TranscriptionJob({
    required this.id,
    required this.createdAt,
    this.originalFilename,
  });

  factory TranscriptionJob.fromJson(Map<String, dynamic> json) =>
      TranscriptionJob(
        id: json['job_id'] as String,
        createdAt: DateTime.parse(json['created_at'] as String),
        originalFilename: json['filename'] as String?,
      );

  @override
  String toString() => 'TranscriptionJob(id: $id)';
}

// ── TranscriptionOptions ──────────────────────────────────────────────────────
// Sent as multipart form fields alongside the audio upload.

class TranscriptionOptions {
  /// Run Demucs source separation before transcribing.
  /// Set false only for single-instrument recordings.
  final bool separateStems;

  /// Specific instrument hints. Empty list = auto-detect all.
  /// Example: ['piano', 'violin']
  final List<String> instruments;

  /// Output format for the score. One of: 'musicxml', 'midi', 'pdf'
  final String outputFormat;

  /// Snap note onsets/durations to a 16th-note grid.
  final bool quantize;

  /// Basic Pitch onset sensitivity. Lower = more notes detected (more false positives).
  /// Higher = only confident onsets (may miss soft notes). Range: 0.0–1.0.
  final double onsetThreshold;

  /// Basic Pitch frame sensitivity. Controls note sustain detection.
  /// Range: 0.0–1.0.
  final double frameThreshold;

  /// Minimum note duration in milliseconds. Notes shorter than this are dropped.
  final int minNoteLengthMs;

  const TranscriptionOptions({
    this.separateStems = true,
    this.instruments = const [],
    this.outputFormat = 'musicxml',
    this.quantize = true,
    this.onsetThreshold = 0.5,
    this.frameThreshold = 0.3,
    this.minNoteLengthMs = 58,
  });

  /// Converts to form fields for multipart upload.
  Map<String, String> toFormFields() => {
        'separate_stems': separateStems.toString(),
        'instruments': instruments.join(','),
        'output_format': outputFormat,
        'quantize': quantize.toString(),
        'onset_threshold': onsetThreshold.toString(),
        'frame_threshold': frameThreshold.toString(),
        'min_note_length_ms': minNoteLengthMs.toString(),
      };
}

// ── JobState ──────────────────────────────────────────────────────────────────

enum JobState {
  pending,    // queued, not yet started
  processing, // actively running
  complete,   // finished successfully
  failed;     // errored out

  bool get isTerminal => this == complete || this == failed;
  bool get isActive => this == pending || this == processing;
}

// ── StageState ────────────────────────────────────────────────────────────────

enum StageState { pending, running, complete, failed }

// ── PipelineStage ─────────────────────────────────────────────────────────────
// One entry per processing stage. Stages are:
//   separation → transcription → instrument_id → notation

class PipelineStage {
  final String id;
  final String label;
  final StageState state;
  final double progress; // 0.0–1.0 within this stage

  const PipelineStage({
    required this.id,
    required this.label,
    required this.state,
    required this.progress,
  });

  factory PipelineStage.fromJson(Map<String, dynamic> json) => PipelineStage(
        id: json['id'] as String,
        label: json['label'] as String,
        state: StageState.values.byName(json['state'] as String),
        progress: (json['progress'] as num).toDouble(),
      );

  PipelineStage copyWith({StageState? state, double? progress}) => PipelineStage(
        id: id,
        label: label,
        state: state ?? this.state,
        progress: progress ?? this.progress,
      );
}

// ── StemInfo ──────────────────────────────────────────────────────────────────
// Populated once Demucs has finished separation (Stage 1 complete).
// Each stem can be previewed via audioUrl.

class StemInfo {
  final String id;       // e.g. 'drums', 'bass', 'piano', 'other_0'
  final String label;    // human-readable, e.g. 'Drums'
  final String audioUrl; // URL to separated stem WAV/MP3 for playback

  const StemInfo({
    required this.id,
    required this.label,
    required this.audioUrl,
  });

  factory StemInfo.fromJson(Map<String, dynamic> json) => StemInfo(
        id: json['id'] as String,
        label: json['label'] as String,
        audioUrl: json['audio_url'] as String,
      );
}

// ── DetectedInstrument ────────────────────────────────────────────────────────

class DetectedInstrument {
  final String name;        // e.g. 'Piano'
  final String stemLabel;   // Demucs stem that triggered this, e.g. 'piano'
  final double confidence;  // 0.0–1.0
  final int noteCount;      // number of notes detected in this stem
  final String emoji;

  const DetectedInstrument({
    required this.name,
    required this.stemLabel,
    required this.confidence,
    required this.noteCount,
    required this.emoji,
  });

  factory DetectedInstrument.fromJson(Map<String, dynamic> json) =>
      DetectedInstrument(
        name: json['name'] as String,
        stemLabel: json['stem_label'] as String,
        confidence: (json['confidence'] as num).toDouble(),
        noteCount: json['note_count'] as int,
        emoji: json['emoji'] as String? ?? '🎵',
      );
}

// ── TranscriptionResult ───────────────────────────────────────────────────────
// Populated only when JobState == complete.

class TranscriptionResult {
  final String musicXmlUrl;
  final String pdfUrl;
  final String midiUrl;
  final List<DetectedInstrument> detectedInstruments;

  const TranscriptionResult({
    required this.musicXmlUrl,
    required this.pdfUrl,
    required this.midiUrl,
    required this.detectedInstruments,
  });

  factory TranscriptionResult.fromJson(Map<String, dynamic> json) =>
      TranscriptionResult(
        musicXmlUrl: json['musicxml_url'] as String,
        pdfUrl: json['pdf_url'] as String,
        midiUrl: json['midi_url'] as String,
        detectedInstruments: (json['detected_instruments'] as List)
            .map((i) => DetectedInstrument.fromJson(i as Map<String, dynamic>))
            .toList(),
      );
}

// ── JobStatus ─────────────────────────────────────────────────────────────────
// Full status snapshot returned by GET /api/v1/jobs/{id}.
// Polled every 2 seconds by TranscriptionScreen.

class JobStatus {
  final String jobId;
  final JobState state;
  final double overallProgress; // 0.0–1.0 across all stages
  final List<PipelineStage> stages;
  final List<StemInfo>? stems;          // non-null after separation completes
  final TranscriptionResult? result;    // non-null when state == complete
  final String? errorMessage;           // non-null when state == failed

  const JobStatus({
    required this.jobId,
    required this.state,
    required this.overallProgress,
    required this.stages,
    this.stems,
    this.result,
    this.errorMessage,
  });

  factory JobStatus.fromJson(Map<String, dynamic> json) => JobStatus(
        jobId: json['job_id'] as String,
        state: JobState.values.byName(json['state'] as String),
        overallProgress: (json['progress'] as num).toDouble(),
        stages: (json['stages'] as List)
            .map((s) => PipelineStage.fromJson(s as Map<String, dynamic>))
            .toList(),
        stems: (json['stems'] as List?)
            ?.map((s) => StemInfo.fromJson(s as Map<String, dynamic>))
            .toList(),
        result: json['result'] != null
            ? TranscriptionResult.fromJson(json['result'] as Map<String, dynamic>)
            : null,
        errorMessage: json['error'] as String?,
      );

  /// Active stage label for display in the UI.
  String get activeStageLabel {
    final running = stages.where((s) => s.state == StageState.running);
    if (running.isNotEmpty) return running.first.label;
    if (state == JobState.complete) return 'Complete';
    if (state == JobState.failed) return 'Failed';
    return 'Waiting…';
  }
}