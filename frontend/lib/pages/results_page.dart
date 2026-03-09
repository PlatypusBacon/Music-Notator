// =============================================================================
// result_screen.dart
// Displays the completed transcription.
//
// Tab 1 — Sheet Music: MusicXML rendered via OpenSheetMusicDisplay (OSMD)
//          inside a WebView. OSMD fetches the MusicXML from the backend URL.
//
// Tab 2 — Instruments: list of detected instruments with confidence + note count.
//
// Downloads: PDF, MusicXML, MIDI saved to device via dio + path_provider.
// =============================================================================

import 'dart:io';
import 'package:flutter/material.dart';
import 'package:webview_flutter/webview_flutter.dart';
import 'package:path_provider/path_provider.dart';
import '../models/transcription_job.dart';
import '../services/api_service.dart';

class ResultScreen extends StatefulWidget {
  const ResultScreen({super.key});

  @override
  State<ResultScreen> createState() => _ResultScreenState();
}

class _ResultScreenState extends State<ResultScreen>
    with SingleTickerProviderStateMixin {
  late final JobStatus _status;
  late final TabController _tabController;
  late final WebViewController _webViewController;
  bool _initialised = false;
  bool _downloading = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (!_initialised) {
      _status = ModalRoute.of(context)!.settings.arguments as JobStatus;
      _tabController = TabController(length: 2, vsync: this);
      _initWebView();
      _initialised = true;
    }
  }

  // ── WebView / OSMD ───────────────────────────────────────────────────────────

  void _initWebView() {
    _webViewController = WebViewController()
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..setBackgroundColor(const Color(0xFF0D0D1A))
      ..setNavigationDelegate(NavigationDelegate(
        onWebResourceError: (e) => debugPrint('WebView error: $e'),
      ))
      ..loadHtmlString(_osmdHtml(_status.result!.musicXmlUrl));
  }

  /// Self-contained HTML page that loads OSMD from jsDelivr CDN and renders
  /// the MusicXML file served by the FastAPI /outputs static mount.
  String _osmdHtml(String musicXmlUrl) => '''
<!DOCTYPE html>
<html lang="en">
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
  <script src="https://cdn.jsdelivr.net/npm/opensheetmusicdisplay@1.8.6/build/opensheetmusicdisplay.min.js"></script>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: #0d0d1a;
      font-family: -apple-system, sans-serif;
      overscroll-behavior: none;
    }
    #osmd { padding: 12px 8px 32px; }
    #loading {
      display: flex; align-items: center; justify-content: center;
      height: 200px; color: rgba(255,255,255,0.4); font-size: 14px;
    }
    #error {
      display: none; padding: 24px; color: #E05C5C; font-size: 13px;
    }
    /* Override OSMD SVG colours for dark background */
    svg text { fill: #e8e8e0 !important; }
    svg line, svg path, svg rect { stroke: #c8c8b8; }
  </style>
</head>
<body>
  <div id="loading">Loading score…</div>
  <div id="error"></div>
  <div id="osmd"></div>
  <script>
    const osmd = new opensheetmusicdisplay.OpenSheetMusicDisplay("osmd", {
      autoResize: true,
      drawTitle: true,
      drawSubtitle: true,
      drawComposer: true,
      drawLyricist: false,
      drawPartNames: true,
      drawCredits: true,
      colorStemsLikeNoteheads: false,
      defaultColorMusic: "#e8e8e0",
      defaultColorTitle: "#D4A847",
      defaultColorLabel: "rgba(255,255,255,0.55)",
    });

    osmd.load("$musicXmlUrl")
      .then(() => {
        document.getElementById("loading").style.display = "none";
        return osmd.render();
      })
      .catch(err => {
        document.getElementById("loading").style.display = "none";
        const el = document.getElementById("error");
        el.style.display = "block";
        el.textContent = "Could not render score: " + err.message;
      });
  </script>
</body>
</html>
''';

  // ── Downloads ────────────────────────────────────────────────────────────────

  Future<void> _download(String url, String filename) async {
    if (_downloading) return;
    setState(() => _downloading = true);

    try {
      final dir = await getApplicationDocumentsDirectory();
      final savePath = '${dir.path}/$filename';
      await ApiService.instance.downloadFile(url, savePath);
      if (!mounted) return;
      _showSnack('Saved to Documents: $filename', success: true);
    } catch (e) {
      if (!mounted) return;
      _showSnack('Download failed: $e', success: false);
    } finally {
      if (mounted) setState(() => _downloading = false);
    }
  }

  void _showSnack(String message, {required bool success}) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text(message),
      backgroundColor: success ? const Color(0xFF4CAF7D) : const Color(0xFFE05C5C),
      behavior: SnackBarBehavior.floating,
      duration: const Duration(seconds: 3),
    ));
  }

  // ── UI ───────────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    final result = _status.result!;

    return Scaffold(
      backgroundColor: const Color(0xFF0D0D1A),
      appBar: AppBar(
        backgroundColor: const Color(0xFF0D0D1A),
        elevation: 0,
        title: const Text('Score', style: TextStyle(
          color: Colors.white,
          fontFamily: 'Playfair Display',
          fontSize: 20,
          fontWeight: FontWeight.w700,
        )),
        iconTheme: const IconThemeData(color: Colors.white54),
        actions: [
          if (_downloading)
            const Padding(
              padding: EdgeInsets.symmetric(horizontal: 16),
              child: SizedBox(
                width: 20, height: 20,
                child: CircularProgressIndicator(
                    strokeWidth: 2, color: Color(0xFFD4A847)),
              ),
            )
          else
            PopupMenuButton<_DownloadTarget>(
              icon: const Icon(Icons.download_rounded, color: Colors.white70),
              color: const Color(0xFF1A1A2E),
              onSelected: (target) => _download(target.url, target.filename),
              itemBuilder: (_) => [
                _menuItem(_DownloadTarget(result.pdfUrl, 'score.pdf'),
                    Icons.picture_as_pdf_rounded, 'Download PDF'),
                _menuItem(_DownloadTarget(result.musicXmlUrl, 'score.musicxml'),
                    Icons.music_note_rounded, 'Download MusicXML'),
                _menuItem(_DownloadTarget(result.midiUrl, 'score.mid'),
                    Icons.piano_rounded, 'Download MIDI'),
              ],
            ),
        ],
        bottom: TabBar(
          controller: _tabController,
          indicatorColor: const Color(0xFFD4A847),
          indicatorWeight: 2,
          labelColor: const Color(0xFFD4A847),
          unselectedLabelColor: Colors.white38,
          labelStyle: const TextStyle(
              fontSize: 13, fontWeight: FontWeight.w600, letterSpacing: 0.3),
          tabs: const [
            Tab(text: 'Sheet Music'),
            Tab(text: 'Instruments'),
          ],
        ),
      ),
      body: TabBarView(
        controller: _tabController,
        children: [
          // ── Tab 1: OSMD WebView ──────────────────────────────────────────
          WebViewWidget(controller: _webViewController),

          // ── Tab 2: Instrument breakdown ──────────────────────────────────
          _InstrumentsTab(instruments: result.detectedInstruments),
        ],
      ),
    );
  }

  PopupMenuItem<_DownloadTarget> _menuItem(
      _DownloadTarget target, IconData icon, String label) {
    return PopupMenuItem(
      value: target,
      child: Row(children: [
        Icon(icon, size: 18, color: Colors.white54),
        const SizedBox(width: 10),
        Text(label, style: const TextStyle(color: Colors.white70, fontSize: 14)),
      ]),
    );
  }

  @override
  void dispose() {
    _tabController.dispose();
    super.dispose();
  }
}

// ── Download target helper ────────────────────────────────────────────────────

class _DownloadTarget {
  final String url;
  final String filename;
  const _DownloadTarget(this.url, this.filename);
}

// ── Instruments tab ───────────────────────────────────────────────────────────

class _InstrumentsTab extends StatelessWidget {
  final List<DetectedInstrument> instruments;
  const _InstrumentsTab({required this.instruments});

  @override
  Widget build(BuildContext context) {
    if (instruments.isEmpty) {
      return const Center(
        child: Text('No instruments detected.',
          style: TextStyle(color: Colors.white38)),
      );
    }

    return ListView.separated(
      padding: const EdgeInsets.fromLTRB(20, 20, 20, 40),
      itemCount: instruments.length,
      separatorBuilder: (_, __) => const Divider(color: Colors.white10, height: 1),
      itemBuilder: (_, i) => _InstrumentTile(instrument: instruments[i]),
    );
  }
}

class _InstrumentTile extends StatelessWidget {
  final DetectedInstrument instrument;
  const _InstrumentTile({required this.instrument});

  @override
  Widget build(BuildContext context) {
    final pct = (instrument.confidence * 100).toInt();

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 14),
      child: Row(
        children: [
          // Emoji badge
          Container(
            width: 46, height: 46,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              color: const Color(0xFF1A1A2E),
              borderRadius: BorderRadius.circular(10),
            ),
            child: Text(instrument.emoji, style: const TextStyle(fontSize: 22)),
          ),
          const SizedBox(width: 14),

          // Name + stem label
          Expanded(child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(instrument.name, style: const TextStyle(
                color: Colors.white,
                fontSize: 15,
                fontWeight: FontWeight.w600,
              )),
              const SizedBox(height: 3),
              Text(
                '${instrument.stemLabel} stem · $pct% confidence',
                style: const TextStyle(color: Colors.white38, fontSize: 12),
              ),
              const SizedBox(height: 6),
              // Confidence bar
              ClipRRect(
                borderRadius: BorderRadius.circular(2),
                child: LinearProgressIndicator(
                  value: instrument.confidence,
                  backgroundColor: Colors.white12,
                  color: _confidenceColor(instrument.confidence),
                  minHeight: 3,
                ),
              ),
            ],
          )),

          const SizedBox(width: 14),

          // Note count
          Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text(
                '${instrument.noteCount}',
                style: const TextStyle(
                  color: Color(0xFFD4A847),
                  fontSize: 20,
                  fontWeight: FontWeight.w700,
                ),
              ),
              const Text('notes',
                style: TextStyle(color: Colors.white38, fontSize: 11)),
            ],
          ),
        ],
      ),
    );
  }

  Color _confidenceColor(double c) {
    if (c >= 0.8) return const Color(0xFF4CAF7D);
    if (c >= 0.6) return const Color(0xFFD4A847);
    return const Color(0xFFE09040);
  }
}