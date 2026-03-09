import 'dart:io';
import 'package:dio/dio.dart';
import '../models/transcription_job.dart';

/// Singleton service handling all communication with the FastAPI backend.
class ApiService {
  ApiService._();
  static final ApiService instance = ApiService._();

  // ── Change this to your deployed backend URL ───────────────────────────────
  static const String _baseUrl = 'http://localhost:8000/api/v1';

  late final Dio _dio = Dio(BaseOptions(
    baseUrl: _baseUrl,
    connectTimeout: const Duration(seconds: 30),
    receiveTimeout: const Duration(minutes: 5),
    headers: {'Accept': 'application/json'},
  ));

  // ── Submit audio for transcription ─────────────────────────────────────────

  Future<TranscriptionJob> submitTranscription({
    required File audioFile,
    required TranscriptionOptions options,
  }) async {
    final formData = FormData.fromMap({
      'audio': await MultipartFile.fromFile(
        audioFile.path,
        filename: audioFile.path.split('/').last,
      ),
      'separate_stems': options.separateStems.toString(),
      'instruments': options.instruments.join(','),
      'output_format': options.outputFormat,
      'quantize': options.quantize.toString(),
    });

    final response = await _dio.post('/transcribe', data: formData);
    return TranscriptionJob.fromJson(response.data as Map<String, dynamic>);
  }

  // ── Poll job status ────────────────────────────────────────────────────────

  Future<JobStatus> getJobStatus(String jobId) async {
    final response = await _dio.get('/jobs/$jobId');
    return JobStatus.fromJson(response.data as Map<String, dynamic>);
  }

  // ── Download output file ───────────────────────────────────────────────────

  Future<void> downloadFile(String url, String saveAs) async {
    // TODO: determine save path via path_provider, request storage permission
    await _dio.download(url, '/tmp/$saveAs');
  }

  // ── Health check (useful on app launch) ───────────────────────────────────

  Future<bool> checkHealth() async {
    try {
      final response = await _dio.get('/health');
      return response.statusCode == 200;
    } catch (_) {
      return false;
    }
  }
}