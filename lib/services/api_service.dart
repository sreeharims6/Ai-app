import 'dart:convert';
import 'dart:io';
import 'package:http/http.dart' as http;
import 'package:device_info_plus/device_info_plus.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Wraps calls to the backend: upload photo, start generation, poll status.
class ApiService {
  // Point this at your deployed FastAPI backend.
  static const String baseUrl = 'https://api.yourapp.com';

  Future<String> _deviceId() async {
    final prefs = await SharedPreferences.getInstance();
    String? id = prefs.getString('device_id');
    if (id != null) return id;

    // Fallback stable-ish id; for production use a proper install UUID
    // generated once and persisted (this pattern is fine for that).
    final info = DeviceInfoPlugin();
    try {
      if (Platform.isIOS) {
        final iosInfo = await info.iosInfo;
        id = iosInfo.identifierForVendor ?? DateTime.now().millisecondsSinceEpoch.toString();
      } else {
        final androidInfo = await info.androidInfo;
        id = androidInfo.id;
      }
    } catch (_) {
      id = DateTime.now().millisecondsSinceEpoch.toString();
    }
    await prefs.setString('device_id', id);
    return id;
  }

  Future<String> uploadPhoto(File image) async {
    final deviceId = await _deviceId();
    final uri = Uri.parse('$baseUrl/upload');
    final request = http.MultipartRequest('POST', uri)
      ..headers['X-Device-Id'] = deviceId
      ..files.add(await http.MultipartFile.fromPath('file', image.path));

    final streamed = await request.send();
    final response = await http.Response.fromStream(streamed);

    if (response.statusCode != 200) {
      throw Exception('Upload failed: ${response.body}');
    }
    final data = jsonDecode(response.body);
    return data['image_key'];
  }

  Future<String> startGeneration(String imageKey, {int motionStrength = 127}) async {
    final deviceId = await _deviceId();
    final uri = Uri.parse('$baseUrl/generate');
    final response = await http.post(
      uri,
      headers: {
        'Content-Type': 'application/json',
        'X-Device-Id': deviceId,
      },
      body: jsonEncode({
        'image_key': imageKey,
        'motion_strength': motionStrength,
        'fps': 7,
        'num_frames': 25,
      }),
    );

    if (response.statusCode == 429) {
      throw Exception('Daily free limit reached. Try again tomorrow!');
    }
    if (response.statusCode != 200) {
      throw Exception('Generation request failed: ${response.body}');
    }
    final data = jsonDecode(response.body);
    return data['job_id'];
  }

  /// Polls until the job is done or failed. Returns the result video URL.
  Future<String> pollUntilDone(String jobId, {Duration interval = const Duration(seconds: 3)}) async {
    final uri = Uri.parse('$baseUrl/status/$jobId');
    while (true) {
      final response = await http.get(uri);
      final data = jsonDecode(response.body);
      final status = data['status'];

      if (status == 'done') {
        return data['result_url'];
      } else if (status == 'failed') {
        throw Exception('Generation failed: ${data['error']}');
      }
      await Future.delayed(interval);
    }
  }
}
