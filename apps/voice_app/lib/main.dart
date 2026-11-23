import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:image_picker/image_picker.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'alerts_settings.dart';
import 'board.dart';
import 'cards.dart';
import 'face.dart';
import 'secretary_page.dart';

/// 바리온 비서: talk to your voice secretary hands-free, or type to it.
///
/// The call itself (microphone, speaker, Bluetooth, Android's call system) runs natively in
/// Kotlin (android/app/src/main/kotlin/.../VoiceEngine.kt). This screen follows the layout the
/// owner asked for on a real call: the conversation takes the whole screen, the status is a small
/// chip in the top right with an ear that moves while you speak, and the call buttons float.
/// Design rules are in docs/voice-app-design.md.
void main() => runApp(const BaryonVoiceApp());

const _channel = MethodChannel('baryon/voice');
const _events = EventChannel('baryon/voice/events');

// ---------------------------------------------------------------------------------------------
// Settings

/// A colour theme. Every colour on screen comes from the theme's ColorScheme, so light themes
/// never show dark leftovers.
class Palette {
  const Palette(
    this.id,
    this.name,
    this.seed, {
    this.brightness = Brightness.dark,
    this.background,
  });
  final String id;
  final String name;
  final Color seed;
  final Brightness brightness;
  final Color? background;
}

const palettes = <Palette>[
  Palette('night', '밤하늘', Color(0xFF7FB0FF), background: Color(0xFF0B1020)),
  Palette('forest', '숲', Color(0xFF6FD9A4), background: Color(0xFF0C1712)),
  Palette('ember', '노을', Color(0xFFFFA45C), background: Color(0xFF1A1210)),
  Palette('contrast', '고대비', Color(0xFFFFE14D), background: Color(0xFF000000)),
  Palette('light', '밝게', Color(0xFF2F6FE4), brightness: Brightness.light),
];

/// Everything the owner can change. Saved on the phone.
class ViewPrefs {
  const ViewPrefs({
    this.scale = 1.0,
    this.paletteId = 'night',
    this.earcons = true,
    this.voice = 0,
    this.speed = 1.05,
    this.filter = 'strict',
    this.keepAwake = true,
    this.haptics = true,
    this.showSteps = true,
    this.autoCall = false,
    this.showFace = true,
  });

  final double scale;
  final String paletteId;
  final bool earcons;
  final int voice;
  final double speed;
  final String filter; // strict: only my voice, normal: any clear voice, off
  final bool keepAwake;
  final bool haptics;
  final bool showSteps;
  final bool autoCall;
  final bool showFace; // the floating face (face.dart)

  Palette get palette => palettes.firstWhere(
    (p) => p.id == paletteId,
    orElse: () => palettes.first,
  );

  ViewPrefs copy({
    double? scale,
    String? paletteId,
    bool? earcons,
    int? voice,
    double? speed,
    String? filter,
    bool? keepAwake,
    bool? haptics,
    bool? showSteps,
    bool? autoCall,
    bool? showFace,
  }) => ViewPrefs(
    scale: scale ?? this.scale,
    paletteId: paletteId ?? this.paletteId,
    earcons: earcons ?? this.earcons,
    voice: voice ?? this.voice,
    speed: speed ?? this.speed,
    filter: filter ?? this.filter,
    keepAwake: keepAwake ?? this.keepAwake,
    haptics: haptics ?? this.haptics,
    showSteps: showSteps ?? this.showSteps,
    autoCall: autoCall ?? this.autoCall,
    showFace: showFace ?? this.showFace,
  );

  static Future<ViewPrefs> load() async {
    try {
      final p = await SharedPreferences.getInstance();
      return ViewPrefs(
        scale: p.getDouble('scale') ?? 1.0,
        paletteId: p.getString('palette') ?? 'night',
        earcons: p.getBool('earcons') ?? true,
        voice: p.getInt('voice') ?? 0,
        speed: p.getDouble('speed') ?? 1.05,
        filter: p.getString('filter') ?? 'strict',
        keepAwake: p.getBool('keepAwake') ?? true,
        haptics: p.getBool('haptics') ?? true,
        showSteps: p.getBool('showSteps') ?? true,
        autoCall: p.getBool('autoCall') ?? false,
        showFace: p.getBool('showFace') ?? true,
      );
    } catch (_) {
      return const ViewPrefs();
    }
  }

  Future<void> save() async {
    try {
      final p = await SharedPreferences.getInstance();
      await p.setDouble('scale', scale);
      await p.setString('palette', paletteId);
      await p.setBool('earcons', earcons);
      await p.setInt('voice', voice);
      await p.setDouble('speed', speed);
      await p.setString('filter', filter);
      await p.setBool('keepAwake', keepAwake);
      await p.setBool('haptics', haptics);
      await p.setBool('showSteps', showSteps);
      await p.setBool('autoCall', autoCall);
      await p.setBool('showFace', showFace);
    } catch (_) {}
  }
}

class BaryonVoiceApp extends StatefulWidget {
  const BaryonVoiceApp({super.key, this.startInChat = false});

  /// Open on the conversation instead of the task board (tests; the "앱을 열면 바로 전화" setting does it too).
  final bool startInChat;

  @override
  State<BaryonVoiceApp> createState() => _BaryonVoiceAppState();
}

class _BaryonVoiceAppState extends State<BaryonVoiceApp> {
  ViewPrefs prefs = const ViewPrefs();

  @override
  void initState() {
    super.initState();
    ViewPrefs.load().then((loaded) {
      if (mounted) setState(() => prefs = loaded);
    });
  }

  void change(ViewPrefs next) {
    setState(() => prefs = next);
    next.save();
  }

  @override
  Widget build(BuildContext context) {
    final palette = prefs.palette;
    final scheme = ColorScheme.fromSeed(
      seedColor: palette.seed,
      brightness: palette.brightness,
    );
    return MaterialApp(
      title: '바리온 비서',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: scheme,
        scaffoldBackgroundColor: palette.background ?? scheme.surface,
        useMaterial3: true,
      ),
      builder: (context, child) => MediaQuery(
        data: MediaQuery.of(
          context,
        ).copyWith(textScaler: TextScaler.linear(prefs.scale)),
        child: child!,
      ),
      home: HomeShell(
        prefs: prefs,
        onPrefs: change,
        startInChat: widget.startInChat,
      ),
    );
  }
}

/// Two tabs: 할 일 (the numbered task board the app opens on) and 대화 (talking or typing to the secretary).
/// Both stay alive while you switch, so a call keeps going when you look at the board.
class HomeShell extends StatefulWidget {
  const HomeShell({
    super.key,
    required this.prefs,
    this.onPrefs,
    this.startInChat = false,
  });

  final ViewPrefs prefs;
  final ValueChanged<ViewPrefs>? onPrefs;
  final bool startInChat;

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  late int tab = widget.startInChat ? 1 : 0;
  late final visited = <int>{tab};
  final outbox = ValueNotifier<String?>(null);
  bool autoCallApplied = false;

  @override
  void didUpdateWidget(HomeShell old) {
    super.didUpdateWidget(old);
    // Settings load after the first frame: "앱을 열면 바로 전화" opens on the conversation once.
    if (!autoCallApplied && widget.prefs.autoCall && !old.prefs.autoCall) {
      autoCallApplied = true;
      show(1);
    }
  }

  @override
  void dispose() {
    outbox.dispose();
    super.dispose();
  }

  void show(int index) => setState(() {
    tab = index;
    visited.add(index);
  });

  /// From the board: open the conversation and send this request.
  void ask(String text) {
    show(1);
    outbox.value = text;
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: IndexedStack(
        index: tab,
        children: [
          visited.contains(0) ? TaskBoard(onAsk: ask) : const SizedBox.shrink(),
          CallScreen(
            prefs: widget.prefs,
            onPrefs: widget.onPrefs,
            outbox: outbox,
          ),
        ],
      ),
      bottomNavigationBar: NavigationBar(
        key: const Key('tabs'),
        height: 60,
        selectedIndex: tab,
        labelBehavior: NavigationDestinationLabelBehavior.alwaysShow,
        onDestinationSelected: show,
        destinations: const [
          NavigationDestination(
            icon: Icon(Icons.checklist_outlined),
            selectedIcon: Icon(Icons.checklist),
            label: '할 일',
          ),
          NavigationDestination(
            icon: Icon(Icons.forum_outlined),
            selectedIcon: Icon(Icons.forum),
            label: '대화',
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------------------------
// Conversation model

/// idle: nothing open · typing: text-only conversation · connected/muted: voice call.
enum Line { idle, connecting, typing, connected, muted, ended }

class Message {
  Message(
    this.fromMe,
    this.text, {
    DateTime? at,
    this.shown,
    List<Map<String, dynamic>>? cards,
  }) : at = at ?? DateTime.now(),
       cards = cards ?? [];
  final bool fromMe;
  String text;
  final DateTime at;

  /// What the owner attached: {kind: image, ref: path} or {kind: recording, seconds, transcript}.
  final Map<String, dynamic>? shown;

  /// What the secretary showed with its answer: lists, tables and charts (cards.dart).
  final List<Map<String, dynamic>> cards;

  Map<String, dynamic> toJson() => {
    'me': fromMe,
    'text': text,
    'at': at.toIso8601String(),
    if (shown != null) 'shown': shown,
    if (cards.isNotEmpty) 'cards': cards,
  };

  static Message fromJson(Map<String, dynamic> json) => Message(
    json['me'] == true,
    json['text'] as String? ?? '',
    at: DateTime.tryParse(json['at'] as String? ?? ''),
    shown: json['shown'] is Map
        ? Map<String, dynamic>.from(json['shown'] as Map)
        : null,
    cards: [
      for (final card in (json['cards'] as List? ?? const []))
        if (card is Map) Map<String, dynamic>.from(card),
    ],
  );
}

class Step {
  Step(this.id, this.label);
  final String id;
  final String label;
  bool done = false;
  bool failed = false;
}

class BackgroundTask {
  BackgroundTask(this.id, this.to, this.summary, this.status, {this.step = ''});
  final String id;
  final String to;
  final String summary;
  String status;
  String step;
}

/// The conversation survives closing the app: the last 200 messages are kept on the phone.
class History {
  static const _key = 'history';
  static const limit = 200;

  static Future<List<Message>> load() async {
    try {
      final raw = (await SharedPreferences.getInstance()).getString(_key);
      if (raw == null) return [];
      return (jsonDecode(raw) as List)
          .map((item) => Message.fromJson(item as Map<String, dynamic>))
          .toList();
    } catch (_) {
      return [];
    }
  }

  static Future<void> save(List<Message> messages) async {
    try {
      final keep = messages.length > limit
          ? messages.sublist(messages.length - limit)
          : messages;
      await (await SharedPreferences.getInstance()).setString(
        _key,
        jsonEncode(keep.map((m) => m.toJson()).toList()),
      );
    } catch (_) {}
  }
}

// ---------------------------------------------------------------------------------------------
// Screen

class CallScreen extends StatefulWidget {
  const CallScreen({
    super.key,
    this.prefs = const ViewPrefs(),
    this.onPrefs,
    this.outbox,
  });

  final ViewPrefs prefs;
  final ValueChanged<ViewPrefs>? onPrefs;

  /// A request from the task board ("3번 작업 어떻게 됐어?"), sent as if typed.
  final ValueNotifier<String?>? outbox;

  @override
  State<CallScreen> createState() => _CallScreenState();
}

class _CallScreenState extends State<CallScreen> {
  Line line = Line.idle;
  String status = '대기 중';
  String? question;
  String? error;
  bool echoCanceller = false;
  String route = 'earpiece';
  bool working = false; // the secretary is thinking or using tools
  bool speaking = false; // the secretary is talking
  bool hearing = false; // the caller is talking (VAD)
  bool dictating = false;
  String liveText = '';
  int ignored = 0; // noise and fragments dropped this call
  int distant = 0; // other people's voices near the phone, dropped this call
  bool replyOpen =
      false; // spoken sentences join the current answer bubble until it is finished
  final steps = <Step>[];
  final tasks = <BackgroundTask>[];
  final messages = <Message>[];
  final scroll = ScrollController();
  final input = TextEditingController();
  StreamSubscription<dynamic>? subscription;
  Timer? saveTimer;
  Map<String, dynamic> config = {};
  // Recording for the secretary (+ > 녹음): seconds so far, input level, and what happens after.
  bool recording = false;
  double recordSeconds = 0;
  double recordLevel = 0;
  String? uploadStage; // sending | transcribing, while a recording becomes text
  double uploadShare = 0;
  String recordAsk = '';
  // The floating face: levels from the phone (VoiceEngine "level"), and where it was dragged to.
  double micLevel = 0;
  double voiceLevel = 0;
  Offset? facePosition;
  bool faceLarge = false;

  bool get inCall =>
      line == Line.connecting || line == Line.connected || line == Line.muted;

  FaceMood get faceMood {
    if (error != null && !working) return FaceMood.error;
    if (question != null) return FaceMood.question;
    if (speaking) return FaceMood.speaking;
    if (working) return FaceMood.thinking;
    final listening = hearing || recording || (inCall && line != Line.muted);
    if (listening) return FaceMood.listening;
    return FaceMood.idle;
  }

  /// The face floats over the conversation; drag it anywhere, tap to make it bigger or smaller.
  Widget floatingFace(BoxConstraints box) {
    final size = faceLarge ? 168.0 : 96.0;
    final position = facePosition ?? Offset(box.maxWidth - size - 8, 56);
    final clamped = Offset(
      position.dx.clamp(0, math.max(0, box.maxWidth - size)).toDouble(),
      position.dy.clamp(0, math.max(0, box.maxHeight - size)).toDouble(),
    );
    return Positioned(
      left: clamped.dx,
      top: clamped.dy,
      child: GestureDetector(
        key: const Key('face'),
        onTap: () => setState(() => faceLarge = !faceLarge),
        onPanUpdate: (drag) =>
            setState(() => facePosition = clamped + drag.delta),
        child: FloatingFace(
          size: size,
          state: FaceState(
            mood: faceMood,
            mic: hearing || recording
                ? (recording ? recordLevel : micLevel)
                : 0,
            voice: speaking ? voiceLevel : 0,
          ),
        ),
      ),
    );
  }

  bool get open => inCall || line == Line.typing;
  ViewPrefs get prefs => widget.prefs;

  @override
  void initState() {
    super.initState();
    subscription = _events.receiveBroadcastStream().listen(
      (raw) => onEvent(jsonDecode(raw as String) as Map<String, dynamic>),
    );
    History.load().then((saved) {
      if (mounted && saved.isNotEmpty) {
        setState(() => messages.insertAll(0, saved));
      }
      jumpToEnd();
    });
    loadConfig();
    checkRecording();
    widget.outbox?.addListener(takeOutbox);
  }

  void takeOutbox() {
    final text = widget.outbox?.value;
    if (text == null || text.isEmpty) return;
    widget.outbox!.value = null;
    input.text = text;
    send();
  }

  /// A recording stopped from its notification while the app was away is sent now.
  Future<void> checkRecording() async {
    try {
      final state =
          await _channel.invokeMapMethod<String, dynamic>('recordState') ?? {};
      if (!mounted) return;
      setState(() {
        recording = state['recording'] == true;
        recordSeconds = (state['seconds'] as num? ?? 0).toDouble();
      });
      final path = state['lastPath'] as String?;
      if (!recording && path != null && path.isNotEmpty) uploadRecording(path);
    } catch (_) {
      // not on Android
    }
  }

  @override
  void didUpdateWidget(CallScreen old) {
    super.didUpdateWidget(old);
    if (old.prefs.earcons != prefs.earcons ||
        old.prefs.voice != prefs.voice ||
        old.prefs.speed != prefs.speed ||
        old.prefs.filter != prefs.filter) {
      sendCallPrefs();
    }
  }

  @override
  void dispose() {
    widget.outbox?.removeListener(takeOutbox);
    subscription?.cancel();
    saveTimer?.cancel();
    input.dispose();
    super.dispose();
  }

  Future<void> loadConfig() async {
    final result =
        await _channel.invokeMapMethod<String, dynamic>('config') ?? {};
    if (!mounted) return;
    setState(() {
      config = result;
      if (result['inCall'] == true) line = Line.connected;
    });
    sendCallPrefs();
    if (prefs.autoCall && !inCall && result['hasToken'] == true) toggleCall();
  }

  void sendCallPrefs() {
    _channel.invokeMethod('callPrefs', {
      'earcons': prefs.earcons,
      'voice': prefs.voice,
      'speed': prefs.speed,
      'filter': prefs.filter,
    });
  }

  void haptic([bool strong = false]) {
    if (!prefs.haptics) return;
    strong ? HapticFeedback.mediumImpact() : HapticFeedback.selectionClick();
  }

  void remember() {
    saveTimer?.cancel();
    saveTimer = Timer(
      const Duration(milliseconds: 500),
      () => History.save(messages),
    );
  }

  void jumpToEnd() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (scroll.hasClients) {
        scroll.animateTo(
          scroll.position.maxScrollExtent,
          duration: const Duration(milliseconds: 200),
          curve: Curves.easeOut,
        );
      }
    });
  }

  void startWork() {
    working = true;
    liveText = '';
    steps.clear();
    status = '생각하는 중';
  }

  void onEvent(Map<String, dynamic> event) {
    setState(() {
      switch (event['type']) {
        case 'state':
          switch (event['state']) {
            case 'connecting':
              line = Line.connecting;
              status = '연결하는 중';
              error = null;
              ignored = 0;
              distant = 0;
            case 'connected':
              line = Line.connected;
              status = '듣고 있어요';
              replyOpen =
                  false; // the greeting is a new bubble, not the end of the last call's answer
              if (prefs.keepAwake) {
                _channel.invokeMethod('keepAwake', {'on': true});
              }
            case 'typing':
              line = Line.typing;
              status = '입력 대화';
            case 'muted':
              line = Line.muted;
              status = '마이크 꺼짐';
            case 'ended':
              line = Line.ended;
              status = '대기 중';
              question = null;
              working = speaking = hearing = dictating = false;
              _channel.invokeMethod('keepAwake', {'on': false});
          }
        case 'mic':
          echoCanceller = event['echo_canceller'] == true;
        case 'route':
          route = event['route'] as String? ?? route;
        case 'vad':
          hearing = event['speaking'] == true;
        case 'ignored':
          event['reason'] == 'distant' ? distant++ : ignored++;
        case 'heard':
          final heard = event['text'] as String? ?? '';
          final last = messages.isNotEmpty ? messages.last : null;
          // A photo or recording, or a typed line, is already in the conversation.
          final shownAlready =
              event['shown'] != null ||
              (last != null &&
                  last.fromMe &&
                  last.text == heard &&
                  DateTime.now().difference(last.at).inSeconds < 60);
          if (!shownAlready) messages.add(Message(true, heard));
          startWork();
          remember();
        case 'card':
          final card = event['card'];
          if (card is Map) {
            final shown = Map<String, dynamic>.from(card);
            if (replyOpen && messages.isNotEmpty && !messages.last.fromMe) {
              messages.last.cards.add(shown);
            } else {
              messages.add(Message(false, '', cards: [shown]));
              replyOpen = true;
            }
            remember();
          }
        case 'recording':
          recording = true;
          recordSeconds = (event['seconds'] as num? ?? 0).toDouble();
          recordLevel = (event['level'] as num? ?? 0).toDouble();
        case 'recorded':
          recording = false;
          uploadRecording(event['path'] as String? ?? '');
        case 'upload':
          uploadStage = event['stage'] as String?;
          final total = (event['total'] as num? ?? 0).toDouble();
          uploadShare = total > 0 ? (event['done'] as num? ?? 0) / total : 0;
        case 'transcript':
          uploadStage = null;
          sendRecording(
            (event['text'] as String? ?? '').trim(),
            (event['seconds'] as num? ?? 0).toDouble(),
          );
        case 'level':
          micLevel = (event['mic'] as num? ?? 0).toDouble();
          voiceLevel = (event['out'] as num? ?? 0).toDouble();
        case 'upload_error':
          uploadStage = null;
          recording = false;
          error = event['message'] as String? ?? '녹음을 보내지 못했어요.';
        case 'activity':
          steps.add(
            Step(
              event['id'] as String? ?? '',
              event['label'] as String? ?? '확인하고 있어요.',
            ),
          );
          status = event['label'] as String? ?? status;
        case 'activity_done':
          for (final step in steps.where((step) => step.id == event['id'])) {
            step.done = true;
            step.failed = event['error'] == true;
          }
        case 'task':
          final id = event['id'] as String? ?? '';
          final existing = tasks
              .where((task) => task.id == id && id.isNotEmpty)
              .firstOrNull;
          if (existing != null) {
            existing.status = event['status'] as String? ?? existing.status;
            existing.step = event['step'] as String? ?? existing.step;
          } else {
            tasks.insert(
              0,
              BackgroundTask(
                id,
                event['to'] as String? ?? '',
                event['summary'] as String? ?? '',
                event['status'] as String? ?? 'sent',
                step: event['step'] as String? ?? '',
              ),
            );
          }
        case 'agent_text':
          liveText += event['delta'] as String? ?? '';
          working = true;
        case 'said':
          final text = event['text'] as String? ?? '';
          if (replyOpen && messages.isNotEmpty && !messages.last.fromMe) {
            messages.last.text = messages.last.text.isEmpty
                ? text
                : '${messages.last.text} $text';
          } else {
            messages.add(Message(false, text));
          }
          replyOpen = true;
          if (inCall) {
            speaking = true;
            status = '말하는 중';
          }
          remember();
        case 'agent_done':
          working = false;
          if (!speaking) replyOpen = false;
          liveText = '';
          status = inCall ? '듣고 있어요' : '입력 대화';
        case 'idle':
          speaking = false;
          if (!working) replyOpen = false; // a later notice gets its own bubble
          if (!working) status = '듣고 있어요';
        case 'confirm':
          question = event['prompt'] as String?;
          working = false;
          status = '대답이 필요해요';
          haptic(true);
        case 'confirmed':
          working = true;
        case 'dictating':
          dictating = event['on'] == true;
        case 'dictation':
          dictating = false;
          final text = (event['text'] as String? ?? '').trim();
          if (text.isNotEmpty) {
            input.text = input.text.isEmpty ? text : '${input.text} $text';
            input.selection = TextSelection.collapsed(
              offset: input.text.length,
            );
          } else {
            error = '말소리를 알아듣지 못했어요.';
          }
        case 'flush':
          speaking = false;
          if (inCall) status = '듣고 있어요';
        case 'error':
          error = event['message'] as String?;
      }
    });
    jumpToEnd();
  }

  Future<void> toggleCall() async {
    haptic(true);
    if (inCall) {
      await _channel.invokeMethod('stop');
    } else {
      setState(() {
        question = null;
        error = null;
      });
      sendCallPrefs();
      await _channel.invokeMethod('start');
    }
  }

  Future<void> send([String? fixed]) async {
    final text = (fixed ?? input.text).trim();
    if (text.isEmpty) return;
    haptic();
    setState(() {
      if (fixed == null) {
        messages.add(Message(true, text));
        input.clear();
        remember();
      }
      question = null;
      error = null;
      startWork();
    });
    jumpToEnd();
    await _channel.invokeMethod('text', {'text': text});
  }

  // --- photos and recordings ---------------------------------------------------------------

  Future<void> openAttach() async {
    haptic();
    final choice = await showModalBottomSheet<String>(
      context: context,
      showDragHandle: true,
      builder: (context) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              key: const Key('attach-camera'),
              leading: const Icon(Icons.photo_camera_outlined),
              title: const Text('사진 찍기'),
              subtitle: const Text('영수증, 명함, 문서, 화이트보드'),
              onTap: () => Navigator.pop(context, 'camera'),
            ),
            ListTile(
              key: const Key('attach-gallery'),
              leading: const Icon(Icons.photo_library_outlined),
              title: const Text('사진 고르기'),
              subtitle: const Text('갤러리, 화면 캡처'),
              onTap: () => Navigator.pop(context, 'gallery'),
            ),
            ListTile(
              key: const Key('attach-record'),
              leading: const Icon(Icons.mic_none),
              title: const Text('녹음하기'),
              subtitle: const Text('회의, 강의, 메모 · 멈추면 글로 옮겨 비서가 정리해요'),
              onTap: () => Navigator.pop(context, 'record'),
            ),
          ],
        ),
      ),
    );
    switch (choice) {
      case 'camera':
        await sendImage(ImageSource.camera);
      case 'gallery':
        await sendImage(ImageSource.gallery);
      case 'record':
        await startRecording();
    }
  }

  Future<void> sendImage(ImageSource source) async {
    final XFile? file;
    try {
      // About 1600 px on the long side is plenty to read text and keeps the upload under a megabyte.
      file = await ImagePicker().pickImage(
        source: source,
        maxWidth: 1600,
        maxHeight: 1600,
        imageQuality: 82,
      );
    } on PlatformException catch (problem) {
      setState(() => error = '사진을 열 수 없어요: ${problem.message ?? problem.code}');
      return;
    }
    if (file == null) return;
    final bytes = await file.readAsBytes();
    final lower = file.path.toLowerCase();
    final type = lower.endsWith('.png')
        ? 'image/png'
        : lower.endsWith('.webp')
        ? 'image/webp'
        : 'image/jpeg';
    final caption = input.text.trim();
    final shown = {'kind': 'image', 'ref': file.path};
    setState(() {
      messages.add(Message(true, caption, shown: shown));
      input.clear();
      question = null;
      error = null;
      startWork();
    });
    remember();
    jumpToEnd();
    await _channel.invokeMethod('attach', {
      'message': jsonEncode({
        'type': 'text',
        'text': caption.isEmpty ? '이 사진을 봐 줘.' : caption,
        'images': [
          {'media_type': type, 'data': base64Encode(bytes)},
        ],
        'shown': shown,
      }),
    });
  }

  Future<void> startRecording() async {
    final problem = await _channel.invokeMethod<String>('recordStart');
    if (!mounted) return;
    setState(() {
      if (problem != null) {
        error = problem;
      } else {
        recording = true;
        recordSeconds = 0;
        recordAsk = input.text
            .trim(); // what to do with it, if the owner already typed it
        input.clear();
      }
    });
  }

  Future<void> stopRecording() async {
    haptic(true);
    await _channel.invokeMethod('recordStop');
  }

  Future<void> uploadRecording(String path) async {
    if (path.isEmpty) return;
    setState(() {
      uploadStage = 'sending';
      uploadShare = 0;
    });
    await _channel.invokeMethod('upload', {'path': path});
  }

  Future<void> sendRecording(String transcript, double seconds) async {
    final ask = recordAsk.isNotEmpty ? recordAsk : input.text.trim();
    recordAsk = '';
    if (transcript.isEmpty) {
      setState(() => error = '녹음에서 말소리를 찾지 못했어요.');
      return;
    }
    final length = AttachmentView.duration(seconds);
    final shown = {
      'kind': 'recording',
      'seconds': seconds,
      'transcript': transcript,
    };
    setState(() {
      messages.add(Message(true, ask, shown: shown));
      if (ask.isNotEmpty && input.text.trim() == ask) input.clear();
      question = null;
      error = null;
      startWork();
    });
    remember();
    jumpToEnd();
    await _channel.invokeMethod('attach', {
      'message': jsonEncode({
        'type': 'text',
        'text':
            '[녹음 $length · 받아쓴 글]\n$transcript\n\n'
            '요청: ${ask.isEmpty ? '핵심 요약, 결정된 것, 할 일을 정리해 줘.' : ask}',
        'shown': {'kind': 'recording', 'seconds': seconds},
      }),
    });
  }

  Widget recordBar(ColorScheme scheme) {
    final uploading = uploadStage != null;
    final minutes = recordSeconds ~/ 60;
    final rest = (recordSeconds % 60).floor().toString().padLeft(2, '0');
    return Container(
      key: const Key('record-bar'),
      margin: const EdgeInsets.fromLTRB(12, 0, 12, 6),
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
      decoration: BoxDecoration(
        color: uploading ? scheme.secondaryContainer : scheme.errorContainer,
        borderRadius: BorderRadius.circular(20),
      ),
      child: Row(
        children: [
          Icon(
            uploading ? Icons.cloud_upload_outlined : Icons.fiber_manual_record,
            color: uploading ? scheme.onSecondaryContainer : scheme.error,
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  uploading
                      ? (uploadStage == 'sending' ? '녹음 보내는 중' : '글로 옮기는 중')
                      : '녹음 중 $minutes:$rest',
                  style: const TextStyle(fontWeight: FontWeight.w600),
                ),
                const SizedBox(height: 4),
                LinearProgressIndicator(
                  value: uploading
                      ? (uploadShare > 0
                            ? uploadShare.clamp(0, 1).toDouble()
                            : null)
                      : (recordLevel * 8).clamp(0, 1).toDouble(),
                  minHeight: 4,
                  borderRadius: BorderRadius.circular(2),
                ),
              ],
            ),
          ),
          if (!uploading)
            TextButton(
              key: const Key('record-stop'),
              onPressed: stopRecording,
              child: const Text('멈추고 보내기'),
            ),
        ],
      ),
    );
  }

  Future<void> toggleDictation() async {
    haptic();
    setState(() => dictating = !dictating);
    await _channel.invokeMethod('dictate', {'on': dictating});
  }

  Future<void> toggleSpeaker() async {
    haptic();
    await _channel.invokeMethod('speaker', {'on': route != 'speaker'});
  }

  String get routeLabel => switch (route) {
    'speaker' => '스피커',
    'bluetooth' => '블루투스',
    'wired' => '이어폰',
    _ => '수화부',
  };

  // --- settings ------------------------------------------------------------------------------

  Future<void> openSettings() async {
    await showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      isScrollControlled: true,
      builder: (context) => StatefulBuilder(
        builder: (context, setSheet) {
          var current = prefs;
          void apply(ViewPrefs next) {
            widget.onPrefs?.call(next);
            setSheet(() => current = next);
          }

          Widget section(String title) => Padding(
            padding: const EdgeInsets.only(top: 16, bottom: 6),
            child: Text(title, style: Theme.of(context).textTheme.titleSmall),
          );

          return DraggableScrollableSheet(
            expand: false,
            initialChildSize: 0.7,
            maxChildSize: 0.95,
            builder: (context, controller) => ListView(
              controller: controller,
              padding: const EdgeInsets.fromLTRB(20, 0, 20, 24),
              children: [
                Card(
                  margin: const EdgeInsets.only(top: 8),
                  child: ListTile(
                    key: const Key('secretary'),
                    leading: const Icon(Icons.badge_outlined),
                    title: const Text('비서에게 알려 주기'),
                    subtitle: const Text('나에 대해 · 이렇게 써 줘 · 기억 · 연결(MCP)'),
                    trailing: const Icon(Icons.chevron_right),
                    onTap: () => Navigator.of(context).push(
                      MaterialPageRoute<void>(
                        builder: (_) => const SecretaryPage(),
                      ),
                    ),
                  ),
                ),
                section('알림'),
                const AlertsSettings(),
                section('화면'),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    for (final palette in palettes)
                      ChoiceChip(
                        key: Key('palette-${palette.id}'),
                        label: Text(palette.name),
                        selected: current.paletteId == palette.id,
                        avatar: CircleAvatar(
                          backgroundColor: palette.seed,
                          radius: 8,
                        ),
                        onSelected: (_) =>
                            apply(current.copy(paletteId: palette.id)),
                      ),
                  ],
                ),
                const SizedBox(height: 8),
                Text('글자 크기 ${(current.scale * 100).round()}%'),
                Slider(
                  key: const Key('text-scale'),
                  value: current.scale,
                  min: 0.85,
                  max: 1.6,
                  divisions: 15,
                  onChanged: (value) => apply(current.copy(scale: value)),
                ),
                SwitchListTile(
                  key: const Key('show-steps'),
                  contentPadding: EdgeInsets.zero,
                  title: const Text('진행 단계 보기'),
                  subtitle: const Text('비서가 무엇을 확인하는지 대화에 표시'),
                  value: current.showSteps,
                  onChanged: (value) => apply(current.copy(showSteps: value)),
                ),
                SwitchListTile(
                  key: const Key('show-face'),
                  contentPadding: EdgeInsets.zero,
                  title: const Text('비서 얼굴'),
                  subtitle: const Text(
                    '듣고 생각하고 말할 때 표정이 바뀌는 떠 있는 얼굴 · 끌어서 옮기고 눌러서 크기 변경',
                  ),
                  value: current.showFace,
                  onChanged: (value) => apply(current.copy(showFace: value)),
                ),
                section('소리'),
                SwitchListTile(
                  key: const Key('earcons'),
                  contentPadding: EdgeInsets.zero,
                  title: const Text('효과음'),
                  subtitle: const Text('띠링 · 동 · 뚝 · 딩동으로 상태 알림'),
                  value: current.earcons,
                  onChanged: (value) => apply(current.copy(earcons: value)),
                ),
                Text('비서 목소리 ${current.voice + 1}번'),
                Slider(
                  key: const Key('voice'),
                  value: current.voice.toDouble(),
                  min: 0,
                  max: 9,
                  divisions: 9,
                  label: '${current.voice + 1}',
                  onChanged: (value) =>
                      apply(current.copy(voice: value.round())),
                ),
                Text('말하는 속도 ${current.speed.toStringAsFixed(2)}×'),
                Slider(
                  key: const Key('speed'),
                  value: current.speed,
                  min: 0.8,
                  max: 1.4,
                  divisions: 12,
                  onChanged: (value) => apply(
                    current.copy(speed: double.parse(value.toStringAsFixed(2))),
                  ),
                ),
                section('듣기'),
                const Text('주변 소리 거르기'),
                const SizedBox(height: 8),
                SegmentedButton<String>(
                  key: const Key('filter'),
                  showSelectedIcon: false,
                  segments: const [
                    ButtonSegment(value: 'strict', label: Text('내 목소리만')),
                    ButtonSegment(value: 'normal', label: Text('또렷한 말')),
                    ButtonSegment(value: 'off', label: Text('끄기')),
                  ],
                  selected: {current.filter},
                  onSelectionChanged: (value) =>
                      apply(current.copy(filter: value.first)),
                ),
                Padding(
                  padding: const EdgeInsets.only(top: 6, bottom: 4),
                  child: Text(switch (current.filter) {
                    'strict' => '휴대폰 가까이에서 말한 목소리만 듣고, 소음과 옆 사람 말은 버려요',
                    'normal' => '소음은 버리고, 또렷한 목소리는 누구 것이든 들어요',
                    _ => '거르지 않아요 (소음이 말로 잘못 들릴 수 있어요)',
                  }, style: Theme.of(context).textTheme.bodySmall),
                ),
                section('통화'),
                SwitchListTile(
                  key: const Key('auto-call'),
                  contentPadding: EdgeInsets.zero,
                  title: const Text('앱을 열면 바로 전화'),
                  subtitle: const Text('차에 거치해 두고 쓸 때'),
                  value: current.autoCall,
                  onChanged: (value) => apply(current.copy(autoCall: value)),
                ),
                SwitchListTile(
                  key: const Key('keep-awake'),
                  contentPadding: EdgeInsets.zero,
                  title: const Text('통화 중 화면 켜 두기'),
                  value: current.keepAwake,
                  onChanged: (value) => apply(current.copy(keepAwake: value)),
                ),
                SwitchListTile(
                  key: const Key('haptics'),
                  contentPadding: EdgeInsets.zero,
                  title: const Text('진동'),
                  value: current.haptics,
                  onChanged: (value) => apply(current.copy(haptics: value)),
                ),
                ListTile(
                  key: const Key('pin-shortcut'),
                  contentPadding: EdgeInsets.zero,
                  leading: const Icon(Icons.add_to_home_screen),
                  title: const Text('홈 화면에 바로가기 추가'),
                  subtitle: const Text('한 번 누르면 바로 비서에게 전화'),
                  onTap: () async {
                    final messenger = ScaffoldMessenger.of(this.context);
                    Navigator.pop(context);
                    final ok =
                        await _channel.invokeMethod<bool>('pinShortcut') ??
                        false;
                    if (!ok) {
                      messenger.showSnackBar(
                        const SnackBar(
                          content: Text('이 런처는 바로가기 추가를 지원하지 않아요.'),
                        ),
                      );
                    }
                  },
                ),
                section('대화 · 연결'),
                ListTile(
                  key: const Key('clear-history'),
                  contentPadding: EdgeInsets.zero,
                  leading: const Icon(Icons.delete_sweep_outlined),
                  title: const Text('대화 내역 지우기'),
                  subtitle: Text('휴대폰에 저장된 ${messages.length}개'),
                  onTap: () {
                    setState(messages.clear);
                    History.save(messages);
                    Navigator.pop(context);
                  },
                ),
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: const Icon(Icons.dns_outlined),
                  title: const Text('서버 설정'),
                  subtitle: Text(
                    open
                        ? '대화 중에는 바꿀 수 없어요'
                        : (config['server'] as String? ?? ''),
                  ),
                  enabled: !open,
                  onTap: () {
                    Navigator.pop(context);
                    openServerSettings();
                  },
                ),
              ],
            ),
          );
        },
      ),
    );
  }

  Future<void> openServerSettings() async {
    final server = TextEditingController(
      text: config['server'] as String? ?? '',
    );
    final token = TextEditingController();
    final saved = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('서버 설정'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
              controller: server,
              decoration: const InputDecoration(
                labelText: '서버 주소 (wss://…/v1/voice/call)',
              ),
            ),
            TextField(
              controller: token,
              obscureText: true,
              decoration: InputDecoration(
                labelText: config['hasToken'] == true
                    ? '기기 토큰 (저장됨, 바꿀 때만 입력)'
                    : '기기 토큰',
              ),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('취소'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('저장'),
          ),
        ],
      ),
    );
    if (saved == true) {
      await _channel.invokeMethod('save', {
        'server': server.text.trim(),
        'token': token.text.trim(),
      });
      await loadConfig();
    }
  }

  // --- layout --------------------------------------------------------------------------------

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final keyboard = MediaQuery.of(context).viewInsets.bottom > 0;
    final busyBubble =
        working || (prefs.showSteps && steps.isNotEmpty && working);
    return Scaffold(
      body: SafeArea(
        child: Stack(
          children: [
            Column(
              children: [
                // Top bar: background tasks on the left, status chip and gear on the right.
                SizedBox(
                  height: 56,
                  child: Row(
                    children: [
                      Expanded(child: _TaskChips(tasks: tasks)),
                      _StatusChip(
                        status: status,
                        line: line,
                        working: working,
                        speaking: speaking,
                        hearing: hearing,
                        detail: inCall
                            ? [
                                routeLabel,
                                if (echoCanceller) '에코 제거',
                                if (ignored > 0) '잡음 $ignored건 무시',
                                if (distant > 0) '주변 말소리 $distant건 거름',
                              ].join(' · ')
                            : null,
                      ),
                      IconButton(
                        key: const Key('settings'),
                        onPressed: openSettings,
                        icon: const Icon(Icons.settings),
                        tooltip: '설정',
                      ),
                    ],
                  ),
                ),
                if (error != null)
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 16),
                    child: Text(error!, style: TextStyle(color: scheme.error)),
                  ),
                // The conversation: the biggest thing on screen.
                Expanded(
                  child: messages.isEmpty && !busyBubble && question == null
                      ? _EmptyHint(inCall: inCall)
                      : ListView.builder(
                          controller: scroll,
                          // in a call, room for the floating speaker and mic buttons
                          padding: EdgeInsets.fromLTRB(
                            12,
                            4,
                            12,
                            inCall ? 112 : 16,
                          ),
                          itemCount:
                              messages.length +
                              (busyBubble ? 1 : 0) +
                              (question != null ? 1 : 0),
                          itemBuilder: (context, index) {
                            if (index < messages.length) {
                              final previous = index > 0
                                  ? messages[index - 1]
                                  : null;
                              return _Bubble(
                                message: messages[index],
                                showTime:
                                    previous == null ||
                                    messages[index].at
                                            .difference(previous.at)
                                            .inMinutes >=
                                        10,
                              );
                            }
                            if (busyBubble && index == messages.length) {
                              return _WorkingBubble(
                                steps: prefs.showSteps ? steps : const [],
                                liveText: liveText,
                              );
                            }
                            return _QuestionCard(
                              question: question!,
                              onAnswer: send,
                            );
                          },
                        ),
                ),
                // Input: typing, with on-prem dictation.
                if (recording || uploadStage != null) recordBar(scheme),
                Padding(
                  padding: EdgeInsets.fromLTRB(4, 4, keyboard ? 12 : 84, 10),
                  child: Row(
                    children: [
                      IconButton(
                        key: const Key('attach'),
                        onPressed: recording || uploadStage != null
                            ? null
                            : openAttach,
                        icon: const Icon(Icons.add_circle_outline),
                        tooltip: '사진 · 녹음',
                      ),
                      Expanded(
                        child: TextField(
                          key: const Key('input'),
                          controller: input,
                          minLines: 1,
                          maxLines: 4,
                          textInputAction: TextInputAction.send,
                          onSubmitted: (_) => send(),
                          onChanged: (_) {
                            if (inCall) _channel.invokeMethod('typing');
                          },
                          decoration: InputDecoration(
                            hintText: dictating
                                ? '말씀하세요… 다시 누르면 받아씁니다'
                                : '비서에게 입력',
                            filled: true,
                            fillColor: scheme.surfaceContainerHigh,
                            border: OutlineInputBorder(
                              borderRadius: BorderRadius.circular(24),
                              borderSide: BorderSide.none,
                            ),
                            contentPadding: const EdgeInsets.symmetric(
                              horizontal: 16,
                              vertical: 12,
                            ),
                            suffixIcon: IconButton(
                              key: const Key('dictate'),
                              onPressed: toggleDictation,
                              icon: Icon(
                                dictating
                                    ? Icons.stop_circle
                                    : Icons.keyboard_voice,
                                color: dictating ? scheme.error : null,
                              ),
                              tooltip: dictating ? '받아쓰기 끝내기' : '말로 입력 (받아쓰기)',
                            ),
                          ),
                        ),
                      ),
                      const SizedBox(width: 8),
                      IconButton.filled(
                        key: const Key('send'),
                        onPressed: send,
                        icon: const Icon(Icons.send),
                        tooltip: '보내기',
                      ),
                    ],
                  ),
                ),
              ],
            ),
            // The secretary's face, floating over the conversation.
            if (prefs.showFace)
              Positioned.fill(
                child: LayoutBuilder(
                  builder: (context, box) =>
                      Stack(children: [floatingFace(box)]),
                ),
              ),
            // Floating call controls, out of the conversation's way.
            if (!keyboard)
              Positioned(
                right: 12,
                bottom: 8,
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    if (inCall) ...[
                      FloatingActionButton.small(
                        heroTag: 'speaker',
                        tooltip: route == 'speaker' ? '스피커 끄기' : '스피커 켜기',
                        backgroundColor: route == 'speaker'
                            ? scheme.primary
                            : scheme.secondaryContainer,
                        foregroundColor: route == 'speaker'
                            ? scheme.onPrimary
                            : scheme.onSecondaryContainer,
                        onPressed: toggleSpeaker,
                        child: Icon(
                          route == 'bluetooth'
                              ? Icons.bluetooth_audio
                              : Icons.volume_up,
                        ),
                      ),
                      const SizedBox(height: 8),
                      FloatingActionButton.small(
                        heroTag: 'mute',
                        tooltip: line == Line.muted ? '마이크 켜기' : '마이크 끄기',
                        onPressed: () => _channel.invokeMethod('mute', {
                          'muted': line != Line.muted,
                        }),
                        child: Icon(
                          line == Line.muted ? Icons.mic_off : Icons.mic,
                        ),
                      ),
                      const SizedBox(height: 8),
                    ],
                    FloatingActionButton(
                      key: const Key('call'),
                      heroTag: 'call',
                      tooltip: inCall ? '통화 종료' : '비서에게 전화',
                      backgroundColor: inCall
                          ? scheme.error
                          : const Color(0xFF2E9E62),
                      foregroundColor: Colors.white,
                      onPressed: line == Line.connecting ? null : toggleCall,
                      child: Icon(inCall ? Icons.call_end : Icons.call),
                    ),
                  ],
                ),
              ),
          ],
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------------------------
// Pieces

/// Top-right status: an ear that keeps moving while it listens (bigger while you speak),
/// a spinner while it works, sound waves while it talks.
class _StatusChip extends StatefulWidget {
  const _StatusChip({
    required this.status,
    required this.line,
    required this.working,
    required this.speaking,
    required this.hearing,
    this.detail,
  });

  final String status;
  final Line line;
  final bool working;
  final bool speaking;
  final bool hearing;
  final String? detail;

  @override
  State<_StatusChip> createState() => _StatusChipState();
}

class _StatusChipState extends State<_StatusChip>
    with SingleTickerProviderStateMixin {
  late final AnimationController pulse;

  @override
  void initState() {
    super.initState();
    pulse = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 900),
    );
    sync();
  }

  bool get listening =>
      widget.line == Line.connected && !widget.working && !widget.speaking;

  /// The ear moves only while it listens; otherwise the animation stops (no wasted frames).
  void sync() {
    if (listening && !pulse.isAnimating) {
      pulse.repeat(reverse: true);
    } else if (!listening && pulse.isAnimating) {
      pulse.stop();
    }
  }

  @override
  void didUpdateWidget(covariant _StatusChip old) {
    super.didUpdateWidget(old);
    sync();
  }

  @override
  void dispose() {
    pulse.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final listening =
        (widget.line == Line.connected) && !widget.working && !widget.speaking;
    Widget icon;
    if (widget.working) {
      icon = const SizedBox(
        width: 16,
        height: 16,
        child: CircularProgressIndicator(strokeWidth: 2),
      );
    } else if (widget.speaking) {
      icon = Icon(Icons.graphic_eq, size: 18, color: scheme.primary);
    } else if (listening) {
      icon = AnimatedBuilder(
        animation: pulse,
        builder: (context, child) {
          final swing = widget.hearing
              ? 0.35
              : 0.12; // moves more while you speak
          return Transform.scale(scale: 1 + swing * pulse.value, child: child);
        },
        child: Icon(
          Icons.hearing,
          key: const Key('ear'),
          size: 18,
          color: widget.hearing ? scheme.primary : scheme.onSurfaceVariant,
        ),
      );
    } else if (widget.line == Line.muted) {
      icon = Icon(Icons.mic_off, size: 18, color: scheme.error);
    } else {
      icon = Icon(Icons.circle_outlined, size: 14, color: scheme.outline);
    }
    return Tooltip(
      message: widget.detail ?? widget.status,
      child: Container(
        key: const Key('status-chip'),
        margin: const EdgeInsets.only(left: 8),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        decoration: BoxDecoration(
          color: scheme.surfaceContainerHigh,
          borderRadius: BorderRadius.circular(20),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            icon,
            const SizedBox(width: 6),
            Text(
              widget.status,
              key: const Key('status'),
              style: const TextStyle(fontSize: 13),
            ),
          ],
        ),
      ),
    );
  }
}

class _TaskChips extends StatelessWidget {
  const _TaskChips({required this.tasks});

  final List<BackgroundTask> tasks;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return ListView.separated(
      scrollDirection: Axis.horizontal,
      padding: const EdgeInsets.only(left: 12),
      itemCount: tasks.length,
      separatorBuilder: (_, _) => const SizedBox(width: 6),
      itemBuilder: (context, index) {
        final task = tasks[index];
        final (icon, color) = switch (task.status) {
          'done' => (Icons.check_circle, scheme.primary),
          'failed' || 'cancelled' => (Icons.error_outline, scheme.error),
          'needs_input' => (Icons.help_outline, scheme.tertiary),
          _ => (Icons.autorenew, scheme.secondary),
        };
        return Center(
          child: Tooltip(
            message: [
              task.to,
              task.summary,
              if (task.step.isNotEmpty) task.step,
            ].join(' · '),
            child: Chip(
              avatar: Icon(icon, size: 16, color: color),
              label: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 120),
                child: Text(
                  task.summary,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontSize: 12),
                ),
              ),
              visualDensity: VisualDensity.compact,
            ),
          ),
        );
      },
    );
  }
}

class _Bubble extends StatelessWidget {
  const _Bubble({required this.message, this.showTime = false});

  final Message message;
  final bool showTime;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final me = message.fromMe;
    return Column(
      crossAxisAlignment: me
          ? CrossAxisAlignment.end
          : CrossAxisAlignment.start,
      children: [
        if (showTime)
          Center(
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: 8),
              child: Text(
                '${message.at.month}/${message.at.day} ${message.at.hour.toString().padLeft(2, '0')}:${message.at.minute.toString().padLeft(2, '0')}',
                style: TextStyle(color: scheme.outline, fontSize: 11),
              ),
            ),
          ),
        if (message.text.isNotEmpty || message.shown != null)
          Container(
            margin: const EdgeInsets.symmetric(vertical: 3),
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
            constraints: BoxConstraints(
              maxWidth: MediaQuery.of(context).size.width * 0.82,
            ),
            decoration: BoxDecoration(
              color: me ? scheme.primaryContainer : scheme.surfaceContainerHigh,
              borderRadius: BorderRadius.only(
                topLeft: const Radius.circular(16),
                topRight: const Radius.circular(16),
                bottomLeft: Radius.circular(me ? 16 : 4),
                bottomRight: Radius.circular(me ? 4 : 16),
              ),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                if (message.shown != null)
                  Padding(
                    padding: EdgeInsets.only(
                      bottom: message.text.isEmpty ? 0 : 6,
                    ),
                    child: AttachmentView(
                      shown: message.shown!,
                      image:
                          message.shown!['kind'] == 'image' &&
                              File(
                                message.shown!['ref'] as String? ?? '',
                              ).existsSync()
                          ? Image.file(
                              File(message.shown!['ref'] as String),
                              fit: BoxFit.cover,
                            )
                          : null,
                    ),
                  ),
                if (message.text.isNotEmpty)
                  SelectableText(
                    message.text,
                    style: TextStyle(
                      fontSize: 16,
                      height: 1.4,
                      color: me ? scheme.onPrimaryContainer : scheme.onSurface,
                    ),
                  ),
                if ((message.shown?['transcript'] as String? ?? '').isNotEmpty)
                  TextButton.icon(
                    key: const Key('transcript'),
                    style: TextButton.styleFrom(
                      padding: EdgeInsets.zero,
                      visualDensity: VisualDensity.compact,
                    ),
                    onPressed: () => showModalBottomSheet<void>(
                      context: context,
                      showDragHandle: true,
                      isScrollControlled: true,
                      builder: (_) => DraggableScrollableSheet(
                        expand: false,
                        initialChildSize: 0.7,
                        builder: (_, controller) => ListView(
                          controller: controller,
                          padding: const EdgeInsets.fromLTRB(20, 0, 20, 24),
                          children: [
                            Text(
                              '받아쓴 글',
                              style: Theme.of(context).textTheme.titleMedium,
                            ),
                            const SizedBox(height: 8),
                            SelectableText(
                              message.shown!['transcript'] as String,
                              style: const TextStyle(fontSize: 15, height: 1.5),
                            ),
                          ],
                        ),
                      ),
                    ),
                    icon: const Icon(Icons.subject, size: 18),
                    label: const Text('받아쓴 글 보기'),
                  ),
              ],
            ),
          ),
        // What the secretary shows rather than says: full width, under its words.
        for (final card in message.cards)
          ConstrainedBox(
            constraints: BoxConstraints(
              maxWidth: MediaQuery.of(context).size.width * 0.94,
            ),
            child: SecretaryCard(card: card),
          ),
      ],
    );
  }
}

/// While the secretary works: the steps it takes and its answer as it forms, in the conversation.
class _WorkingBubble extends StatelessWidget {
  const _WorkingBubble({required this.steps, required this.liveText});

  final List<Step> steps;
  final String liveText;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final recent = steps.length > 4 ? steps.sublist(steps.length - 4) : steps;
    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        key: const Key('working'),
        margin: const EdgeInsets.symmetric(vertical: 3),
        padding: const EdgeInsets.all(12),
        constraints: BoxConstraints(
          maxWidth: MediaQuery.of(context).size.width * 0.82,
        ),
        decoration: BoxDecoration(
          border: Border.all(color: scheme.outlineVariant),
          borderRadius: BorderRadius.circular(16),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            if (recent.isEmpty)
              Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const SizedBox(
                    width: 14,
                    height: 14,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  ),
                  const SizedBox(width: 8),
                  Text(
                    '생각하는 중…',
                    style: TextStyle(color: scheme.onSurfaceVariant),
                  ),
                ],
              ),
            for (final step in recent)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 2),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(
                      step.failed
                          ? Icons.error_outline
                          : step.done
                          ? Icons.check_circle
                          : Icons.more_horiz,
                      size: 16,
                      color: step.failed
                          ? scheme.error
                          : step.done
                          ? scheme.primary
                          : scheme.outline,
                    ),
                    const SizedBox(width: 6),
                    Flexible(
                      child: Text(
                        step.label,
                        style: TextStyle(
                          fontSize: 13,
                          color: scheme.onSurfaceVariant,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            if (liveText.trim().isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 6),
                child: Text(
                  liveText.trim(),
                  maxLines: 4,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    color: scheme.onSurfaceVariant,
                    fontStyle: FontStyle.italic,
                    fontSize: 13,
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _QuestionCard extends StatelessWidget {
  const _QuestionCard({required this.question, required this.onAnswer});

  final String question;
  final ValueChanged<String> onAnswer;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      margin: const EdgeInsets.symmetric(vertical: 6),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: scheme.tertiaryContainer,
        borderRadius: BorderRadius.circular(16),
      ),
      child: Column(
        children: [
          Text(
            question,
            style: TextStyle(fontSize: 17, color: scheme.onTertiaryContainer),
            textAlign: TextAlign.center,
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(
                child: FilledButton(
                  onPressed: () => onAnswer('응'),
                  style: FilledButton.styleFrom(
                    minimumSize: const Size.fromHeight(52),
                  ),
                  child: const Text('응, 진행해', style: TextStyle(fontSize: 17)),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: OutlinedButton(
                  onPressed: () => onAnswer('아니'),
                  style: OutlinedButton.styleFrom(
                    minimumSize: const Size.fromHeight(52),
                  ),
                  child: const Text('아니', style: TextStyle(fontSize: 17)),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _EmptyHint extends StatelessWidget {
  const _EmptyHint({required this.inCall});

  final bool inCall;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              inCall ? Icons.hearing : Icons.forum_outlined,
              size: 40,
              color: scheme.outline,
            ),
            const SizedBox(height: 12),
            Text(
              inCall
                  ? '듣고 있어요. 편하게 말씀하세요.'
                  : '오른쪽 아래 버튼으로 전화하거나,\n아래에 입력해 보세요.',
              textAlign: TextAlign.center,
              style: TextStyle(color: scheme.onSurfaceVariant),
            ),
            const SizedBox(height: 16),
            Text(
              '"열린 PR이랑 CI 알려줘" · "할 일 세 개만" · "로그인 버그 맡겨 줘"',
              textAlign: TextAlign.center,
              style: TextStyle(color: scheme.outline, fontSize: 12),
            ),
          ],
        ),
      ),
    );
  }
}
