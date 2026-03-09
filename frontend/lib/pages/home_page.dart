import 'dart:io';
import 'package:flutter/material.dart';
import 'package:file_picker/file_picker.dart';
import 'package:record/record.dart';
import '../services/api_service.dart';
import '../models/transcription_job.dart';
import '../widgets/waveform_widget.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  final AudioRecorder _recorder = AudioRecorder();
  bool _isRecording = false;
  String? _recordedPath;
  File? _selectedFile;

  // ── Recording ──────────────────────────────────────────────────────────────

  Future<void> _startRecording() async {
    if (!await _recorder.hasPermission()) return;
    final path = '/tmp/scorescribe_recording.wav'; // TODO: use path_provider
    await _recorder.start(const RecordConfig(encoder: AudioEncoder.wav), path: path);
    setState(() => _isRecording = true);
  }

  Future<void> _stopRecording() async {
    final path = await _recorder.stop();
    setState(() {
      _isRecording = false;
      _recordedPath = path;
      _selectedFile = path != null ? File(path) : null;
    });
  }

  // ── File Picking ───────────────────────────────────────────────────────────

  Future<void> _pickFile() async {
    final result = await FilePicker.platform.pickFiles(
      type: FileType.custom,
      allowedExtensions: ['wav', 'mp3', 'flac', 'aiff', 'm4a'],
    );
    if (result != null && result.files.single.path != null) {
      setState(() => _selectedFile = File(result.files.single.path!));
    }
  }

  // ── Submit to Backend ──────────────────────────────────────────────────────

  Future<void> _submitForTranscription() async {
    if (_selectedFile == null) return;

    // Options the user can configure (expand into a settings sheet)
    final options = TranscriptionOptions(
      separateStems: true,      // run Demucs source separation first
      instruments: [],          // empty = auto-detect all
      outputFormat: 'musicxml',
      quantize: true,
    );

    try {
      final job = await ApiService.instance.submitTranscription(
        audioFile: _selectedFile!,
        options: options,
      );

      if (!mounted) return;
      Navigator.pushNamed(context, '/transcribe', arguments: job);
    } catch (e) {
      // TODO: show error snackbar
      debugPrint('Submission error: $e');
    }
  }

  // ── UI ─────────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF0D0D1A),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Header
              const Text('ScoreScribe', style: TextStyle(
                fontFamily: 'Playfair Display',
                fontSize: 36,
                fontWeight: FontWeight.w700,
                color: Color(0xFFE8D5B7),
                letterSpacing: -1,
              )),
              const SizedBox(height: 4),
              Text('Audio → Sheet Music, automatically.',
                style: TextStyle(color: Colors.white.withOpacity(0.5), fontSize: 14)),
              const SizedBox(height: 48),

              // Record / Upload cards
              _InputCard(
                icon: Icons.mic_rounded,
                label: _isRecording ? 'Recording…' : 'Record Audio',
                subtitle: 'Tap to capture live audio',
                active: _isRecording,
                onTap: _isRecording ? _stopRecording : _startRecording,
              ),
              const SizedBox(height: 16),
              _InputCard(
                icon: Icons.audio_file_rounded,
                label: _selectedFile != null
                    ? _selectedFile!.path.split('/').last
                    : 'Upload Audio File',
                subtitle: 'WAV, MP3, FLAC, AIFF, M4A',
                active: _selectedFile != null,
                onTap: _pickFile,
              ),

              const SizedBox(height: 24),

              // Waveform preview
              if (_selectedFile != null) ...[
                WaveformWidget(file: _selectedFile!),
                const SizedBox(height: 24),
              ],

              const Spacer(),

              // Transcribe button
              SizedBox(
                width: double.infinity,
                height: 56,
                child: FilledButton(
                  onPressed: _selectedFile != null ? _submitForTranscription : null,
                  style: FilledButton.styleFrom(
                    backgroundColor: const Color(0xFFD4A847),
                    foregroundColor: Colors.black,
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                  ),
                  child: const Text('Transcribe', style: TextStyle(
                    fontSize: 16, fontWeight: FontWeight.w700, letterSpacing: 0.5,
                  )),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

// ── Small reusable card ────────────────────────────────────────────────────────

class _InputCard extends StatelessWidget {
  final IconData icon;
  final String label;
  final String subtitle;
  final bool active;
  final VoidCallback onTap;

  const _InputCard({
    required this.icon, required this.label, required this.subtitle,
    required this.active, required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        padding: const EdgeInsets.all(20),
        decoration: BoxDecoration(
          color: active ? const Color(0xFF1E1E3A) : const Color(0xFF13131F),
          borderRadius: BorderRadius.circular(16),
          border: Border.all(
            color: active ? const Color(0xFFD4A847) : Colors.white12,
            width: active ? 1.5 : 1,
          ),
        ),
        child: Row(
          children: [
            Icon(icon, color: active ? const Color(0xFFD4A847) : Colors.white38, size: 28),
            const SizedBox(width: 16),
            Expanded(child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(label, style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w600)),
                const SizedBox(height: 2),
                Text(subtitle, style: TextStyle(color: Colors.white.withOpacity(0.4), fontSize: 12)),
              ],
            )),
          ],
        ),
      ),
    );
  }
}