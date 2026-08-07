import 'package:flutter/material.dart';
import 'screens/home_screen.dart';

void main() {
  runApp(const AiVideoMakerApp());
}

class AiVideoMakerApp extends StatelessWidget {
  const AiVideoMakerApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'AI Video Maker',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorSchemeSeed: Colors.deepPurple,
        useMaterial3: true,
      ),
      home: const HomeScreen(),
    );
  }
}
