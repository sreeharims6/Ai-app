import 'dart:io';
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:video_player/video_player.dart';
import '../services/api_service.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

enum _Stage { idle, uploading, generating, done, error }

class _HomeScreenState extends State<HomeScreen> {
  final _api = ApiService();
  final _picker = ImagePicker();

  File? _selectedImage;
  _Stage _stage = _Stage.idle;
  String? _errorMessage;
  VideoPlayerController? _videoController;

  Future<void> _pickImage() async {
    final picked = await _picker.pickImage(source: ImageSource.gallery, imageQuality: 90);
    if (picked == null) return;
    setState(() {
      _selectedImage = File(picked.path);
      _stage = _Stage.idle;
      _videoController?.dispose();
      _videoController = null;
    });
  }

  Future<void> _generate() async {
    if (_selectedImage == null) return;
    setState(() => _stage = _Stage.uploading);

    try {
      final imageKey = await _api.uploadPhoto(_selectedImage!);

      setState(() => _stage = _Stage.generating);
      final jobId = await _api.startGeneration(imageKey);
      final resultUrl = await _api.pollUntilDone(jobId);

      final controller = VideoPlayerController.networkUrl(Uri.parse(resultUrl));
      await controller.initialize();
      controller.setLooping(true);
      controller.play();

      setState(() {
        _videoController = controller;
        _stage = _Stage.done;
      });
    } catch (e) {
      setState(() {
        _errorMessage = e.toString();
        _stage = _Stage.error;
      });
    }
  }

  @override
  void dispose() {
    _videoController?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('AI Video Maker')),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          children: [
            Expanded(child: _buildPreview()),
            const SizedBox(height: 16),
            _buildControls(),
          ],
        ),
      ),
    );
  }

  Widget _buildPreview() {
    if (_stage == _Stage.done && _videoController != null) {
      return AspectRatio(
        aspectRatio: _videoController!.value.aspectRatio,
        child: VideoPlayer(_videoController!),
      );
    }
    if (_selectedImage != null) {
      return ClipRRect(
        borderRadius: BorderRadius.circular(12),
        child: Image.file(_selectedImage!, fit: BoxFit.cover),
      );
    }
    return const Center(child: Text('Pick a photo to animate it ✨'));
  }

  Widget _buildControls() {
    switch (_stage) {
      case _Stage.uploading:
        return const _ProgressRow(label: 'Uploading photo...');
      case _Stage.generating:
        return const _ProgressRow(label: 'Generating your video (this can take ~30-60s)...');
      case _Stage.error:
        return Column(
          children: [
            Text(_errorMessage ?? 'Something went wrong', style: const TextStyle(color: Colors.red)),
            const SizedBox(height: 8),
            _actionButtons(),
          ],
        );
      case _Stage.idle:
      case _Stage.done:
        return _actionButtons();
    }
  }

  Widget _actionButtons() {
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceEvenly,
      children: [
        ElevatedButton.icon(
          onPressed: _pickImage,
          icon: const Icon(Icons.photo_library),
          label: const Text('Choose Photo'),
        ),
        ElevatedButton.icon(
          onPressed: _selectedImage == null ? null : _generate,
          icon: const Icon(Icons.auto_awesome),
          label: const Text('Animate'),
        ),
      ],
    );
  }
}

class _ProgressRow extends StatelessWidget {
  final String label;
  const _ProgressRow({required this.label});

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        const CircularProgressIndicator(),
        const SizedBox(height: 8),
        Text(label, textAlign: TextAlign.center),
      ],
    );
  }
}
