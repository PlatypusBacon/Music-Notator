// =============================================================================
// transcription_screen.dart
// Shown while the backend processes the audio. Polls GET /api/v1/jobs/{id}
// every 2 seconds and drives animated stage rows + stem preview cards.
// Auto-navigates to ResultScreen when JobState == complete.
// =============================================================================

import 'dart:async';
import 'package:flutter/material.dart';
import '../models/transcription_job.dart';
import '../services/api_service.dart';
import '../widgets/stem_card.dart';

class TranscriptionScreen extends StatefulWidget {
  const TranscriptionScreen({super.key});

  @override
  State<TranscriptionScreen> createState() => _TranscriptionScreenState();
}

class _TranscriptionScreenState extends State<TranscriptionScreen>
    with TickerProviderStateMixin {
  late final TranscriptionJob _job;
  Timer? _pollTimer;
  JobStatus? _status;
  bool _initialised = false;

  // Animated progress bar controller
  late final AnimationController _progressCtrl = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 600),
  );
  late Animation<double> _progressAnim =
      Tween<double>(begin: 0, end: 0).animate(_progressCtrl);

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (!_initialised) {
      _job = ModalRoute.of(context)!.settings.arguments as TranscriptionJob;
      _initialised = true;
      _startPolling();
    }
  }

  // ── Polling ─────────────────────────────────────────────────────────────────

  void _startPolling() {
    // Immediate first poll, then every 2 s
    _poll();
    _pollTimer = Timer.periodic(const Duration(seconds: 2), (_) => _poll());
  }

  Future<void> _poll() async {
    try {
      final status = await ApiService.instance.getJobStatus(_job.id);
      if (!mounted) return;

      // Animate progress bar to new value
      final newProgress = status.overallProgress.clamp(0.0, 1.0);
      _progressAnim = Tween<double>(
        begin: _progressAnim.value,
        end: newProgress,
      ).animate(CurvedAnimation(parent: _progressCtrl, curve: Curves.easeOut));
      _progressCtrl.forward(from: 0);

      setState(() => _status = status);

      if (status.state.isTerminal) {
        _pollTimer?.cancel();
        if (!mounted) return;
        if (status.state == JobState.complete) {
          // Small delay so user sees 100% before navigating
          await Future.delayed(const Duration(milliseconds: 800));
          if (!mounted) return;
          Navigator.pushReplacementNamed(context, '/result', arguments: status);
        }
      }
    } catch (e) {
      // Network blip — keep polling
      debugPrint('Poll error: $e');
    }
  }

  @override
  void dispose() {
    _pollTimer?.cancel();
    _progressCtrl.dispose();
    super.dispose();
  }

  // ── Build ────────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    final stages = _status?.stages ?? _placeholderStages();
    final stems = _status?.stems ?? [];
    final activeLabel = _status?.activeStageLabel ?? 'Initialising…';
    final hasFailed = _status?.state == JobState.failed;

    return Scaffold(
      backgroundColor: const Color(0xFF0D0D1A),
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        automaticallyImplyLeading: false,
        title: Text(
          hasFailed ? 'Transcription Failed' : 'Processing',
          style: const TextStyle(color: Colors.white70, fontSize: 17),
        ),
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(24, 8, 24, 32),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // ── Overall progress ──────────────────────────────────────────
            AnimatedBuilder(
              animation: _progressAnim,
              builder: (_, _) => Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  ClipRRect(
                    borderRadius: BorderRadius.circular(4),
                    child: LinearProgressIndicator(
                      value: _status == null ? null : _progressAnim.value,
                      backgroundColor: Colors.white12,
                      color: hasFailed
                          ? const Color(0xFFE05C5C)
                          : const Color(0xFFD4A847),
                      minHeight: 4,
                    ),
                  ),
                  const SizedBox(height: 10),
                  Text(
                    activeLabel,
                    style: TextStyle(
                      color: Colors.white.withOpacity(0.45),
                      fontSize: 12,
                      letterSpacing: 0.3,
                    ),
                  ),
                ],
              ),
            ),

            const SizedBox(height: 36),

            // ── Pipeline stages ───────────────────────────────────────────
            _SectionLabel('PIPELINE STAGES'),
            const SizedBox(height: 12),
            ...stages.asMap().entries.map((e) =>
                _StageRow(stage: e.value, index: e.key)),

            // ── Error message ─────────────────────────────────────────────
            if (hasFailed && _status?.errorMessage != null) ...[
              const SizedBox(height: 24),
              Container(
                padding: const EdgeInsets.all(16),
                decoration: BoxDecoration(
                  color: const Color(0xFF2A1515),
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: const Color(0xFFE05C5C).withOpacity(0.4)),
                ),
                child: Row(
                  children: [
                    const Icon(Icons.error_outline_rounded,
                        color: Color(0xFFE05C5C), size: 20),
                    const SizedBox(width: 12),
                    Expanded(child: Text(
                      _status!.errorMessage!,
                      style: const TextStyle(color: Color(0xFFE05C5C), fontSize: 13),
                    )),
                  ],
                ),
              ),
              const SizedBox(height: 16),
              SizedBox(
                width: double.infinity,
                child: OutlinedButton(
                  onPressed: () => Navigator.pop(context),
                  style: OutlinedButton.styleFrom(
                    foregroundColor: Colors.white54,
                    side: const BorderSide(color: Colors.white24),
                    shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(10)),
                  ),
                  child: const Text('Go Back'),
                ),
              ),
            ],

            // ── Stems (appear once separation finishes) ───────────────────
            if (stems.isNotEmpty) ...[
              const SizedBox(height: 36),
              _SectionLabel('SEPARATED STEMS'),
              const SizedBox(height: 12),
              ...stems.map((stem) => Padding(
                    padding: const EdgeInsets.only(bottom: 10),
                    child: StemCard(stem: stem),
                  )),
            ],
          ],
        ),
      ),
    );
  }

  List<PipelineStage> _placeholderStages() => const [
        PipelineStage(id: 'separation',    label: 'Source Separation',       state: StageState.pending, progress: 0),
        PipelineStage(id: 'transcription', label: 'Pitch Transcription',     state: StageState.pending, progress: 0),
        PipelineStage(id: 'instrument_id', label: 'Instrument Detection',    state: StageState.pending, progress: 0),
        PipelineStage(id: 'notation',      label: 'Sheet Music Generation',  state: StageState.pending, progress: 0),
      ];
}

// ── Section label ─────────────────────────────────────────────────────────────

class _SectionLabel extends StatelessWidget {
  final String text;
  const _SectionLabel(this.text);

  @override
  Widget build(BuildContext context) => Text(
        text,
        style: const TextStyle(
          color: Colors.white38,
          fontSize: 11,
          fontWeight: FontWeight.w600,
          letterSpacing: 1.8,
        ),
      );
}

// ── Stage row ─────────────────────────────────────────────────────────────────

class _StageRow extends StatelessWidget {
  final PipelineStage stage;
  final int index;

  const _StageRow({required this.stage, required this.index});

  @override
  Widget build(BuildContext context) {
    final (icon, color) = switch (stage.state) {
      StageState.pending  => (Icons.radio_button_unchecked_rounded, Colors.white24),
      StageState.running  => (Icons.autorenew_rounded,              const Color(0xFFD4A847)),
      StageState.complete => (Icons.check_circle_rounded,           const Color(0xFF4CAF7D)),
      StageState.failed   => (Icons.error_rounded,                  const Color(0xFFE05C5C)),
    };

    final isRunning = stage.state == StageState.running;

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 9),
      child: Row(
        children: [
          // Stage number badge
          Container(
            width: 26,
            height: 26,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              color: stage.state == StageState.pending
                  ? Colors.white.withOpacity(0.05)
                  : color.withOpacity(0.15),
              borderRadius: BorderRadius.circular(6),
            ),
            child: stage.state == StageState.running
                ? SizedBox(
                    width: 14, height: 14,
                    child: CircularProgressIndicator(
                      strokeWidth: 2,
                      value: stage.progress > 0 ? stage.progress : null,
                      color: color,
                    ),
                  )
                : Icon(icon, color: color, size: 16),
          ),
          const SizedBox(width: 14),

          // Label
          Expanded(
            child: Text(
              stage.label,
              style: TextStyle(
                color: stage.state == StageState.pending
                    ? Colors.white38
                    : Colors.white.withOpacity(0.9),
                fontSize: 14,
                fontWeight: isRunning ? FontWeight.w600 : FontWeight.w400,
              ),
            ),
          ),

          // Progress percentage (only while running)
          if (isRunning && stage.progress > 0)
            Text(
              '${(stage.progress * 100).toInt()}%',
              style: const TextStyle(
                color: Color(0xFFD4A847),
                fontSize: 12,
                fontWeight: FontWeight.w500,
              ),
            ),

          if (stage.state == StageState.complete)
            const Text(
              'Done',
              style: TextStyle(color: Color(0xFF4CAF7D), fontSize: 12),
            ),
        ],
      ),
    );
  }
}