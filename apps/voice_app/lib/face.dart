import 'dart:io';
import 'dart:math' as math;
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';

/// What the face shows. The screen decides the mood from the call; levels come from the phone's
/// microphone and speaker (VoiceEngine "level" events), about 15 times a second.
enum FaceMood { idle, listening, thinking, speaking, question, error }

class FaceState {
  const FaceState({this.mood = FaceMood.idle, this.mic = 0, this.voice = 0});
  final FaceMood mood;
  final double mic; // 0..1 how loud the caller is
  final double voice; // 0..1 how loud the secretary is
}

/// 바리온 비서's face: the brand's B turned on its side (spine = brow, bowls = eyes) on a glossy
/// navy sphere (shaders/face.frag). It floats, turns its head now and then, blinks, listens with
/// ripples, thinks with circling dots and moves its mouth with the voice.
class FloatingFace extends StatefulWidget {
  const FloatingFace({super.key, required this.state, this.size = 96});

  final FaceState state;
  final double size;

  /// No animation in widget tests (pumpAndSettle would never settle).
  static final bool animated = !Platform.environment.containsKey(
    'FLUTTER_TEST',
  );

  @override
  State<FloatingFace> createState() => _FloatingFaceState();
}

class _Target {
  _Target(
    this.eye,
    this.smile,
    this.brow,
    this.think,
    this.glow,
    this.lookX,
    this.lookY,
  );
  final double eye, smile, brow, think, glow, lookX, lookY;
}

class _FloatingFaceState extends State<FloatingFace>
    with SingleTickerProviderStateMixin {
  static ui.FragmentProgram? program;
  static bool loading = false;
  Ticker? ticker;
  Duration last = Duration.zero;
  double time = 0;
  final random = math.Random();

  // Smoothed values, eased toward the mood's targets every frame.
  double eye = 0,
      smile = 0.4,
      brow = 0,
      think = 0,
      glow = 0,
      mouth = 0,
      listen = 0,
      blink = 0;
  double lookX = 0, lookY = 0;
  double nextBlink = 2.5, blinkStart = -1;
  double wanderX = 0, wanderY = 0, nextWander = 3;

  @override
  void initState() {
    super.initState();
    if (program == null && !loading && FloatingFace.animated) {
      loading = true;
      ui.FragmentProgram.fromAsset('shaders/face.frag')
          .then((loaded) {
            program = loaded;
            if (mounted) setState(() {});
          })
          .catchError((Object _) {}); // the painted fallback stays
    }
    if (FloatingFace.animated) {
      ticker = createTicker(tick)..start();
    }
  }

  @override
  void dispose() {
    ticker?.dispose();
    super.dispose();
  }

  _Target target(FaceMood mood) => switch (mood) {
    FaceMood.idle => _Target(0, 0.45, 0, 0, 0, wanderX, wanderY),
    FaceMood.listening => _Target(1, 0.2, 0.1, 0, 0.15, 0, -0.1),
    FaceMood.thinking => _Target(0.7, 0, 0.15, 1, 0.1, 0.35, 0.4),
    FaceMood.speaking => _Target(0.85, 0.3, 0, 0, 0.8, wanderX * 0.5, 0),
    FaceMood.question => _Target(1, 0.1, 1, 0, 0.3, 0.1, 0),
    FaceMood.error => _Target(0.9, -0.6, -0.8, 0, 0, 0, -0.2),
  };

  void tick(Duration elapsed) {
    final dt = ((elapsed - last).inMicroseconds / 1e6).clamp(0.0, 0.1);
    last = elapsed;
    time += dt;
    final goal = target(widget.state.mood);
    double ease(double value, double to, double rate) =>
        value + (to - value) * (1 - math.exp(-rate * dt));
    eye = ease(eye, goal.eye, 8);
    smile = ease(smile, goal.smile, 6);
    brow = ease(brow, goal.brow, 7);
    think = ease(think, goal.think, 5);
    glow = ease(glow, goal.glow, 6);
    lookX = ease(lookX, goal.lookX, 3);
    lookY = ease(lookY, goal.lookY, 3);
    listen = ease(
      listen,
      widget.state.mood == FaceMood.listening
          ? math.max(widget.state.mic * 6, 0.15).clamp(0, 1)
          : 0,
      10,
    );
    // The mouth follows the voice; with no level (typed answers) it chatters gently while speaking.
    var open = (widget.state.voice * 7).clamp(0.0, 1.0);
    if (widget.state.mood == FaceMood.speaking && widget.state.voice == 0) {
      open = 0.35 + 0.3 * math.sin(time * 13) * math.sin(time * 5.3);
    }
    if (widget.state.mood != FaceMood.speaking) open = 0;
    mouth = ease(mouth, open, 18);
    // Look around now and then while idle; blink every few seconds.
    if (time > nextWander) {
      wanderX = random.nextDouble() * 0.8 - 0.4;
      wanderY = random.nextDouble() * 0.4 - 0.15;
      nextWander = time + 2.5 + random.nextDouble() * 3;
    }
    if (time > nextBlink) {
      blinkStart = time;
      nextBlink = time + 2.5 + random.nextDouble() * 4;
    }
    final since = time - blinkStart;
    blink = blinkStart >= 0 && since < 0.16
        ? math.sin(since / 0.16 * math.pi)
        : 0;
    setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    final bob = math.sin(time * 1.6);
    return SizedBox.square(
      dimension: widget.size,
      child: CustomPaint(
        painter: program != null
            ? _ShaderFace(program!.fragmentShader(), [
                time,
                mouth,
                listen,
                eye,
                smile,
                brow,
                think,
                blink,
                lookX,
                lookY,
                glow,
                bob,
              ])
            : _PlainFace(widget.state.mood),
      ),
    );
  }
}

class _ShaderFace extends CustomPainter {
  _ShaderFace(this.shader, this.values);

  final ui.FragmentShader shader;
  final List<double> values;

  @override
  void paint(Canvas canvas, Size size) {
    shader
      ..setFloat(0, size.width)
      ..setFloat(1, size.height);
    for (var i = 0; i < values.length; i++) {
      shader.setFloat(2 + i, values[i]);
    }
    canvas.drawRect(Offset.zero & size, Paint()..shader = shader);
  }

  @override
  bool shouldRepaint(covariant CustomPainter old) => true;
}

/// Before the shader loads (and in tests): the same face, flat.
class _PlainFace extends CustomPainter {
  _PlainFace(this.mood);
  final FaceMood mood;

  @override
  void paint(Canvas canvas, Size size) {
    final c = size.center(Offset.zero);
    final r = size.shortestSide * 0.33;
    canvas.drawCircle(c, r, Paint()..color = const Color(0xFF13203C));
    final ink = Paint()
      ..color = const Color(0xFFF7F0DB)
      ..style = PaintingStyle.stroke
      ..strokeWidth = r * 0.1
      ..strokeCap = StrokeCap.round;
    final browY = c.dy - r * 0.2;
    canvas.drawLine(
      Offset(c.dx - r * 0.37, browY),
      Offset(c.dx + r * 0.37, browY),
      ink,
    );
    for (final side in [-1.0, 1.0]) {
      canvas.drawArc(
        Rect.fromCircle(
          center: Offset(c.dx + side * r * 0.18, browY),
          radius: r * 0.17,
        ),
        0,
        math.pi,
        false,
        ink,
      );
    }
    final mouth = Path()
      ..moveTo(c.dx - r * 0.17, c.dy + r * 0.28)
      ..quadraticBezierTo(
        c.dx,
        c.dy + r * (mood == FaceMood.error ? 0.2 : 0.38),
        c.dx + r * 0.17,
        c.dy + r * 0.28,
      );
    canvas.drawPath(mouth, ink);
  }

  @override
  bool shouldRepaint(_PlainFace old) => old.mood != mood;
}
