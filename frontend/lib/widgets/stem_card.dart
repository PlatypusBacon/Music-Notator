// =============================================================================
// stem_card.dart
// Card shown per separated audio stem on the TranscriptionScreen.
//
// Features
// ────────
// • Play / pause the separated stem audio from the backend URL (just_audio)
// • Live playback progress bar that scrubs on tap
// • Duration display (elapsed / total)
// • Per-stem colour + icon theming (drums=red, bass=blue, etc.)
// • Loading / error states
// • Properly disposes AudioPlayer on widget removal
//
// Dependency (pubspec.yaml):
//   just_audio: ^0.9.39
// =============================================================================

import 'dart:async';
import 'package:flutter/material.dart';
import 'package:just_audio/just_audio.dart';
import '../models/transcription_job.dart';

class StemCard extends StatefulWidget {
  final StemInfo stem;

  const StemCard({super.key, required this.stem});

  @override
  State<StemCard> createState() => _StemCardState();
}

class _StemCardState extends State<StemCard> {
  late final AudioPlayer _player;
  StreamSubscription<PlayerState>? _stateSub;
  StreamSubscription<Duration?>? _durationSub;

  PlayerState? _playerState;
  Duration _position = Duration.zero;
  Duration _duration = Duration.zero;
  bool _loading = true;
  String? _error;

  // ── Stem theming ────────────────────────────────────────────────────────────

  static const _stemThemes = <String, _StemTheme>{
    'drums':  _StemTheme(color: Color(0xFFE05C5C), icon: Icons.sports_bar_rounded,    emoji: '🥁'),
    'bass':   _StemTheme(color: Color(0xFF5B8DEF), icon: Icons.queue_music_rounded,   emoji: '🎸'),
    'vocals': _StemTheme(color: Color(0xFFB57BEA), icon: Icons.mic_rounded,           emoji: '🎤'),
    'guitar': _StemTheme(color: Color(0xFF4CAF7D), icon: Icons.music_note_rounded,    emoji: '🎸'),
    'piano':  _StemTheme(color: Color(0xFFD4A847), icon: Icons.piano_rounded,         emoji: '🎹'),
    'other':  _StemTheme(color: Color(0xFF7B9EA8), icon: Icons.graphic_eq_rounded,    emoji: '🎵'),
  };

  _StemTheme get _theme {
    final key = widget.stem.id.toLowerCase().replaceAll(RegExp(r'_\d+$'), '');
    return _stemThemes[key] ?? _stemThemes['other']!;
  }

  // ── Lifecycle ────────────────────────────────────────────────────────────────

  @override
  void initState() {
    super.initState();
    _player = AudioPlayer();
    _initPlayer();
  }

  Future<void> _initPlayer() async {
    try {
      await _player.setUrl(widget.stem.audioUrl);
      _stateSub = _player.playerStateStream.listen((state) {
        if (mounted) setState(() => _playerState = state);
      });
      _durationSub = _player.durationStream.listen((d) {
        if (mounted) setState(() => _duration = d ?? Duration.zero);
      });
      _player.positionStream.listen((p) {
        if (mounted) setState(() => _position = p);
      });
      if (mounted) setState(() => _loading = false);
    } catch (e) {
      if (mounted) setState(() { _loading = false; _error = 'Could not load audio'; });
    }
  }

  @override
  void dispose() {
    _stateSub?.cancel();
    _durationSub?.cancel();
    _player.dispose();
    super.dispose();
  }

  // ── Playback control ─────────────────────────────────────────────────────────

  Future<void> _togglePlayback() async {
    if (_error != null || _loading) return;
    if (_player.playing) {
      await _player.pause();
    } else {
      // If at end, restart
      if (_position >= _duration && _duration > Duration.zero) {
        await _player.seek(Duration.zero);
      }
      await _player.play();
    }
  }

  Future<void> _seekTo(double fraction) async {
    if (_duration == Duration.zero) return;
    final target = _duration * fraction.clamp(0.0, 1.0);
    await _player.seek(target);
  }

  // ── Helpers ──────────────────────────────────────────────────────────────────

  bool get _isPlaying => _playerState?.playing == true;

  bool get _isBuffering =>
      _playerState?.processingState == ProcessingState.buffering ||
      _playerState?.processingState == ProcessingState.loading;

  double get _progressFraction {
    if (_duration == Duration.zero) return 0.0;
    return (_position.inMilliseconds / _duration.inMilliseconds).clamp(0.0, 1.0);
  }

  String _formatDuration(Duration d) {
    final m = d.inMinutes.remainder(60).toString().padLeft(2, '0');
    final s = d.inSeconds.remainder(60).toString().padLeft(2, '0');
    return '$m:$s';
  }

  // ── Build ────────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    final theme = _theme;

    return AnimatedContainer(
      duration: const Duration(milliseconds: 200),
      decoration: BoxDecoration(
        color: _isPlaying
            ? theme.color.withOpacity(0.08)
            : const Color(0xFF13131F),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(
          color: _isPlaying ? theme.color.withOpacity(0.4) : Colors.white10,
          width: _isPlaying ? 1.5 : 1.0,
        ),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          // ── Main row ───────────────────────────────────────────────────────
          Padding(
            padding: const EdgeInsets.fromLTRB(14, 12, 8, 8),
            child: Row(
              children: [
                // Stem colour dot + emoji
                _StemBadge(theme: theme, isPlaying: _isPlaying),
                const SizedBox(width: 12),

                // Label + duration
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        widget.stem.label,
                        style: TextStyle(
                          color: _isPlaying ? Colors.white : Colors.white.withOpacity(0.85),
                          fontSize: 14,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                      const SizedBox(height: 2),
                      Text(
                        _loading
                            ? 'Loading…'
                            : _error != null
                                ? _error!
                                : _duration == Duration.zero
                                    ? '--:--'
                                    : '${_formatDuration(_position)}  /  ${_formatDuration(_duration)}',
                        style: TextStyle(
                          color: _error != null
                              ? const Color(0xFFE05C5C)
                              : theme.color.withOpacity(_isPlaying ? 0.9 : 0.55),
                          fontSize: 11,
                          fontWeight: FontWeight.w500,
                          fontFeatures: const [FontFeature.tabularFigures()],
                        ),
                      ),
                    ],
                  ),
                ),

                // Play / pause / loading button
                _PlayButton(
                  isPlaying: _isPlaying,
                  isLoading: _loading || _isBuffering,
                  hasError: _error != null,
                  color: theme.color,
                  onTap: _togglePlayback,
                ),
              ],
            ),
          ),

          // ── Progress scrubber ──────────────────────────────────────────────
          if (!_loading && _error == null)
            _ProgressScrubber(
              progress: _progressFraction,
              color: theme.color,
              isPlaying: _isPlaying,
              onSeek: _seekTo,
            ),
        ],
      ),
    );
  }
}

// ── Sub-widgets ───────────────────────────────────────────────────────────────

class _StemBadge extends StatelessWidget {
  final _StemTheme theme;
  final bool isPlaying;

  const _StemBadge({required this.theme, required this.isPlaying});

  @override
  Widget build(BuildContext context) {
    return AnimatedContainer(
      duration: const Duration(milliseconds: 200),
      width: 38,
      height: 38,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: theme.color.withOpacity(isPlaying ? 0.22 : 0.12),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Text(theme.emoji, style: const TextStyle(fontSize: 18)),
    );
  }
}

class _PlayButton extends StatelessWidget {
  final bool isPlaying;
  final bool isLoading;
  final bool hasError;
  final Color color;
  final VoidCallback onTap;

  const _PlayButton({
    required this.isPlaying,
    required this.isLoading,
    required this.hasError,
    required this.color,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: 44,
      height: 44,
      child: hasError
          ? Icon(Icons.error_outline_rounded, color: Colors.white24, size: 26)
          : isLoading
              ? Padding(
                  padding: const EdgeInsets.all(12),
                  child: CircularProgressIndicator(
                    strokeWidth: 2,
                    color: color.withOpacity(0.6),
                  ),
                )
              : IconButton(
                  icon: Icon(
                    isPlaying
                        ? Icons.pause_circle_rounded
                        : Icons.play_circle_rounded,
                    color: isPlaying ? color : Colors.white38,
                    size: 32,
                  ),
                  padding: EdgeInsets.zero,
                  onPressed: onTap,
                ),
    );
  }
}

class _ProgressScrubber extends StatelessWidget {
  final double progress;
  final Color color;
  final bool isPlaying;
  final ValueChanged<double> onSeek;

  const _ProgressScrubber({
    required this.progress,
    required this.color,
    required this.isPlaying,
    required this.onSeek,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTapDown: (d) {
        final box = context.findRenderObject() as RenderBox;
        final fraction = (d.localPosition.dx / box.size.width).clamp(0.0, 1.0);
        onSeek(fraction);
      },
      onHorizontalDragUpdate: (d) {
        final box = context.findRenderObject() as RenderBox;
        final fraction = (d.localPosition.dx / box.size.width).clamp(0.0, 1.0);
        onSeek(fraction);
      },
      child: Padding(
        padding: const EdgeInsets.fromLTRB(14, 0, 14, 12),
        child: Stack(
          alignment: Alignment.centerLeft,
          children: [
            // Track background
            Container(
              height: 3,
              decoration: BoxDecoration(
                color: Colors.white10,
                borderRadius: BorderRadius.circular(2),
              ),
            ),
            // Filled portion
            FractionallySizedBox(
              widthFactor: progress,
              child: Container(
                height: 3,
                decoration: BoxDecoration(
                  color: isPlaying ? color : color.withOpacity(0.4),
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
            ),
            // Thumb — only show while playing
            if (isPlaying)
              Positioned(
                left: (MediaQuery.of(context).size.width - 56) * progress - 5,
                child: Container(
                  width: 10,
                  height: 10,
                  decoration: BoxDecoration(
                    color: color,
                    shape: BoxShape.circle,
                    boxShadow: [BoxShadow(color: color.withOpacity(0.5), blurRadius: 4)],
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

// ── Theme data ────────────────────────────────────────────────────────────────

class _StemTheme {
  final Color color;
  final IconData icon;
  final String emoji;

  const _StemTheme({
    required this.color,
    required this.icon,
    required this.emoji,
  });
}