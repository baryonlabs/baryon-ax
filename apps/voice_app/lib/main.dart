import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 바리온 비서: talk to your voice secretary hands-free, or type to it.
///
/// The call itself (microphone, speaker, Bluetooth, Android's call system) runs natively in
/// Kotlin (android/app/src/main/kotlin/.../VoiceEngine.kt). This screen shows what the secretary
/// is doing right now, the work it handed off in the background, and the conversation.
void main() => runApp(const BaryonVoiceApp());

const _channel = MethodChannel('baryon/voice');
const _events = EventChannel('baryon/voice/events');

/// A colour theme. Add one here and it shows up in the view settings.
class Palette {
  const Palette(
    this.id,
    this.name,
    this.background,
    this.surface,
    this.accent, {
    this.brightness = Brightness.dark,
  });
  final String id;
  final String name;
  final Color background;
  final Color surface;
  final Color accent;
  final Brightness brightness;
}

const palettes = <Palette>[
  Palette(
    'night',
    '밤하늘',
    Color(0xFF0B1020),
    Color(0xFF16233A),
    Color(0xFF7FB0FF),
  ),
  Palette(
    'forest',
    '숲',
    Color(0xFF0C1712),
    Color(0xFF16291F),
    Color(0xFF6FD9A4),
  ),
  Palette(
    'ember',
    '노을',
    Color(0xFF1A1210),
    Color(0xFF2B1D19),
    Color(0xFFFFA45C),
  ),
  Palette(
    'contrast',
    '고대비',
    Color(0xFF000000),
    Color(0xFF1F1F1F),
    Color(0xFFFFE14D),
  ),
  Palette(
    'light',
    '밝게',
    Color(0xFFF4F6FA),
    Color(0xFFE3E9F3),
    Color(0xFF2F6FE4),
    brightness: Brightness.light,
  ),
];

enum LayoutMode { auto, stacked, split }

/// How the screen looks: text size, colours, layout. Saved on the phone.
class ViewPrefs {
  const ViewPrefs({
    this.scale = 1.0,
    this.paletteId = 'night',
    this.layout = LayoutMode.auto,
    this.compact = true,
  });
  final double scale;
  final String paletteId;
  final LayoutMode layout;
  final bool compact;

  Palette get palette => palettes.firstWhere(
    (p) => p.id == paletteId,
    orElse: () => palettes.first,
  );

  ViewPrefs copy({
    double? scale,
    String? paletteId,
    LayoutMode? layout,
    bool? compact,
  }) => ViewPrefs(
    scale: scale ?? this.scale,
    paletteId: paletteId ?? this.paletteId,
    layout: layout ?? this.layout,
    compact: compact ?? this.compact,
  );

  static Future<ViewPrefs> load() async {
    try {
      final p = await SharedPreferences.getInstance();
      return ViewPrefs(
        scale: p.getDouble('scale') ?? 1.0,
        paletteId: p.getString('palette') ?? 'night',
        layout: LayoutMode.values.firstWhere(
          (m) => m.name == p.getString('layout'),
          orElse: () => LayoutMode.auto,
        ),
        compact: p.getBool('compact') ?? true,
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
      await p.setString('layout', layout.name);
      await p.setBool('compact', compact);
    } catch (_) {}
  }
}

class BaryonVoiceApp extends StatefulWidget {
  const BaryonVoiceApp({super.key});

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
    return MaterialApp(
      title: '바리온 비서',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
          seedColor: palette.accent,
          brightness: palette.brightness,
        ),
        scaffoldBackgroundColor: palette.background,
        cardColor: palette.surface,
        useMaterial3: true,
      ),
      builder: (context, child) => MediaQuery(
        data: MediaQuery.of(
          context,
        ).copyWith(textScaler: TextScaler.linear(prefs.scale)),
        child: child!,
      ),
      home: CallScreen(prefs: prefs, onPrefs: change),
    );
  }
}

/// idle: nothing open · typing: text-only conversation · connected/muted: voice call.
enum Line { idle, connecting, typing, connected, muted, ended }

class Message {
  Message(this.fromMe, this.text);
  final bool fromMe;
  String text;
}

class Step {
  Step(this.id, this.label);
  final String id;
  final String label;
  bool done = false;
  bool failed = false;
}

class BackgroundTask {
  BackgroundTask(this.to, this.summary, this.status);
  final String to;
  final String summary;
  final String status;
}

class CallScreen extends StatefulWidget {
  const CallScreen({super.key, this.prefs = const ViewPrefs(), this.onPrefs});

  final ViewPrefs prefs;
  final ValueChanged<ViewPrefs>? onPrefs;

  @override
  State<CallScreen> createState() => _CallScreenState();
}

class _CallScreenState extends State<CallScreen> {
  Line line = Line.idle;
  String status = '전화하거나 아래에 입력해 보세요.';
  String? question;
  String? error;
  bool echoCanceller = false;
  String route = 'earpiece';
  bool working = false; // the secretary is thinking or using tools
  bool dictating = false;
  String liveText = '';
  final steps = <Step>[];
  final tasks = <BackgroundTask>[];
  final messages = <Message>[];
  final scroll = ScrollController();
  final input = TextEditingController();
  StreamSubscription<dynamic>? subscription;
  Map<String, dynamic> config = {};

  bool get inCall =>
      line == Line.connecting || line == Line.connected || line == Line.muted;
  bool get open => inCall || line == Line.typing;

  @override
  void initState() {
    super.initState();
    subscription = _events.receiveBroadcastStream().listen(
      (raw) => onEvent(jsonDecode(raw as String) as Map<String, dynamic>),
    );
    loadConfig();
  }

  @override
  void dispose() {
    subscription?.cancel();
    input.dispose();
    super.dispose();
  }

  Future<void> loadConfig() async {
    final result =
        await _channel.invokeMapMethod<String, dynamic>('config') ?? {};
    setState(() {
      config = result;
      if (result['inCall'] == true) line = Line.connected;
    });
  }

  void startWork() {
    working = true;
    liveText = '';
    steps.clear();
    status = '생각하는 중…';
  }

  void onEvent(Map<String, dynamic> event) {
    setState(() {
      switch (event['type']) {
        case 'state':
          switch (event['state']) {
            case 'connecting':
              line = Line.connecting;
              status = '연결하는 중…';
              error = null;
            case 'connected':
              line = Line.connected;
              status = '듣고 있어요. 말씀하세요.';
            case 'typing':
              line = Line.typing;
              status = '입력으로 대화 중';
            case 'muted':
              line = Line.muted;
              status = '마이크가 꺼져 있어요.';
            case 'ended':
              line = Line.ended;
              status = '대화가 끝났습니다.';
              question = null;
              working = false;
              dictating = false;
          }
        case 'mic':
          echoCanceller = event['echo_canceller'] == true;
        case 'route':
          route = event['route'] as String? ?? route;
        case 'heard':
          messages.add(Message(true, event['text'] as String? ?? ''));
          startWork();
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
          tasks.insert(
            0,
            BackgroundTask(
              event['to'] as String? ?? '',
              event['summary'] as String? ?? '',
              event['status'] as String? ?? 'sent',
            ),
          );
        case 'agent_text':
          liveText += event['delta'] as String? ?? '';
          working = true;
        case 'said':
          final text = event['text'] as String? ?? '';
          if (messages.isNotEmpty && !messages.last.fromMe) {
            messages.last.text = '${messages.last.text} $text';
          } else {
            messages.add(Message(false, text));
          }
          if (inCall) status = '말하는 중 · 말씀하시면 멈춥니다';
        case 'agent_done':
          working = false;
          liveText = '';
          status = inCall ? '듣고 있어요. 말씀하세요.' : '입력으로 대화 중';
        case 'idle':
          if (!working) status = '듣고 있어요. 말씀하세요.';
        case 'confirm':
          question = event['prompt'] as String?;
          working = false;
          status = '확인이 필요해요.';
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
          if (inCall) status = '듣고 있어요.';
        case 'error':
          error = event['message'] as String?;
      }
    });
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

  Future<void> toggleCall() async {
    HapticFeedback.mediumImpact();
    if (inCall) {
      await _channel.invokeMethod('stop');
    } else {
      setState(() {
        question = null;
        error = null;
      });
      await _channel.invokeMethod('start');
    }
  }

  Future<void> send([String? fixed]) async {
    final text = (fixed ?? input.text).trim();
    if (text.isEmpty) return;
    HapticFeedback.selectionClick();
    setState(() {
      if (fixed == null) {
        messages.add(Message(true, text));
        input.clear();
      }
      question = null;
      error = null;
      startWork();
    });
    await _channel.invokeMethod('text', {'text': text});
  }

  Future<void> toggleDictation() async {
    HapticFeedback.selectionClick();
    setState(() => dictating = !dictating);
    await _channel.invokeMethod('dictate', {'on': dictating});
  }

  Future<void> toggleSpeaker() async {
    HapticFeedback.selectionClick();
    await _channel.invokeMethod('speaker', {'on': route != 'speaker'});
  }

  String get routeLabel => switch (route) {
    'speaker' => '스피커',
    'bluetooth' => '블루투스',
    'wired' => '이어폰',
    _ => '수화부',
  };

  /// The floating gear: how the screen looks, and (outside a call) the server.
  Future<void> openSettings() async {
    await showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      isScrollControlled: true,
      builder: (context) => StatefulBuilder(
        builder: (context, setSheet) {
          var prefs = widget.prefs;
          void apply(ViewPrefs next) {
            widget.onPrefs?.call(next);
            setSheet(() => prefs = next);
          }

          return SafeArea(
            child: SingleChildScrollView(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(20, 0, 20, 16),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      '화면 테마',
                      style: Theme.of(context).textTheme.titleSmall,
                    ),
                    const SizedBox(height: 8),
                    Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: [
                        for (final palette in palettes)
                          ChoiceChip(
                            key: Key('palette-${palette.id}'),
                            label: Text(palette.name),
                            selected: prefs.paletteId == palette.id,
                            avatar: CircleAvatar(
                              backgroundColor: palette.accent,
                              radius: 8,
                            ),
                            onSelected: (_) =>
                                apply(prefs.copy(paletteId: palette.id)),
                          ),
                      ],
                    ),
                    const SizedBox(height: 16),
                    Text(
                      '글자 크기 ${(prefs.scale * 100).round()}%',
                      style: Theme.of(context).textTheme.titleSmall,
                    ),
                    Slider(
                      key: const Key('text-scale'),
                      value: prefs.scale,
                      min: 0.85,
                      max: 1.6,
                      divisions: 15,
                      onChanged: (value) => apply(prefs.copy(scale: value)),
                    ),
                    const SizedBox(height: 4),
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
              ),
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

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    // While the keyboard is up, keep the conversation and the input; hide the call controls.
    final keyboard = MediaQuery.of(context).viewInsets.bottom > 0;
    return Scaffold(
      body: Stack(
        children: [
          SafeArea(
            child: Column(
              children: [
                _ProgressCard(
                  rightInset: 56, // room for the floating settings button
                  compact: keyboard,
                  status: status,
                  working: working,
                  steps: steps,
                  liveText: liveText,
                  detail: inCall
                      ? [routeLabel, if (echoCanceller) '에코 제거 켜짐'].join(' · ')
                      : null,
                  error: error,
                ),
                if (tasks.isNotEmpty) _TaskStrip(tasks: tasks),
                Expanded(
                  child: ListView.builder(
                    controller: scroll,
                    padding: const EdgeInsets.symmetric(
                      horizontal: 16,
                      vertical: 8,
                    ),
                    itemCount: messages.length,
                    itemBuilder: (context, index) {
                      final message = messages[index];
                      return Align(
                        alignment: message.fromMe
                            ? Alignment.centerRight
                            : Alignment.centerLeft,
                        child: Container(
                          margin: const EdgeInsets.symmetric(vertical: 4),
                          padding: const EdgeInsets.symmetric(
                            horizontal: 14,
                            vertical: 10,
                          ),
                          constraints: BoxConstraints(
                            maxWidth: MediaQuery.of(context).size.width * 0.8,
                          ),
                          decoration: BoxDecoration(
                            color: message.fromMe
                                ? scheme.primaryContainer
                                : Theme.of(context).cardColor,
                            borderRadius: BorderRadius.circular(14),
                          ),
                          child: Text(
                            message.text,
                            style: const TextStyle(fontSize: 16, height: 1.4),
                          ),
                        ),
                      );
                    },
                  ),
                ),
                if (question != null)
                  Container(
                    margin: const EdgeInsets.fromLTRB(16, 4, 16, 0),
                    padding: const EdgeInsets.all(16),
                    decoration: BoxDecoration(
                      color: const Color(0xFF3A2E14),
                      borderRadius: BorderRadius.circular(16),
                    ),
                    child: Column(
                      children: [
                        Text(
                          question!,
                          style: const TextStyle(fontSize: 17),
                          textAlign: TextAlign.center,
                          maxLines: keyboard ? 2 : 4,
                          overflow: TextOverflow.ellipsis,
                        ),
                        const SizedBox(height: 12),
                        Row(
                          children: [
                            Expanded(
                              child: FilledButton(
                                onPressed: () => send('응'),
                                style: FilledButton.styleFrom(
                                  minimumSize: const Size.fromHeight(56),
                                ),
                                child: const Text(
                                  '응, 진행해',
                                  style: TextStyle(fontSize: 18),
                                ),
                              ),
                            ),
                            const SizedBox(width: 12),
                            Expanded(
                              child: OutlinedButton(
                                onPressed: () => send('아니'),
                                style: OutlinedButton.styleFrom(
                                  minimumSize: const Size.fromHeight(56),
                                ),
                                child: const Text(
                                  '아니',
                                  style: TextStyle(fontSize: 18),
                                ),
                              ),
                            ),
                          ],
                        ),
                      ],
                    ),
                  ),
                // Typing, with on-prem dictation into the box.
                Padding(
                  padding: const EdgeInsets.fromLTRB(12, 8, 12, 0),
                  child: Row(
                    children: [
                      Expanded(
                        child: TextField(
                          key: const Key('input'),
                          controller: input,
                          minLines: 1,
                          maxLines: 4,
                          textInputAction: TextInputAction.send,
                          onSubmitted: (_) => send(),
                          decoration: InputDecoration(
                            hintText: dictating
                                ? '말씀하세요… 다시 누르면 받아씁니다'
                                : '비서에게 입력',
                            filled: true,
                            fillColor: Theme.of(context).cardColor,
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
                if (!keyboard)
                  Padding(
                    padding: const EdgeInsets.fromLTRB(24, 12, 24, 8),
                    child: Row(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        if (inCall) ...[
                          IconButton.filledTonal(
                            iconSize: 28,
                            isSelected: route == 'speaker',
                            onPressed: toggleSpeaker,
                            icon: Icon(
                              route == 'bluetooth'
                                  ? Icons.bluetooth_audio
                                  : Icons.volume_up,
                            ),
                            tooltip: route == 'speaker' ? '스피커 끄기' : '스피커 켜기',
                          ),
                          const SizedBox(width: 20),
                          IconButton.filledTonal(
                            iconSize: 28,
                            onPressed: () => _channel.invokeMethod('mute', {
                              'muted': line != Line.muted,
                            }),
                            icon: Icon(
                              line == Line.muted ? Icons.mic_off : Icons.mic,
                            ),
                            tooltip: line == Line.muted ? '마이크 켜기' : '마이크 끄기',
                          ),
                          const SizedBox(width: 20),
                        ],
                        SizedBox(
                          width: 80,
                          height: 80,
                          child: FilledButton(
                            onPressed: line == Line.connecting
                                ? null
                                : toggleCall,
                            style: FilledButton.styleFrom(
                              shape: const CircleBorder(),
                              padding: EdgeInsets.zero,
                              backgroundColor: inCall
                                  ? const Color(0xFFD9534F)
                                  : const Color(0xFF3BB273),
                            ),
                            child: Icon(
                              inCall ? Icons.call_end : Icons.call,
                              size: 38,
                              color: Colors.white,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                if (!keyboard)
                  Text(
                    inCall ? '통화 종료' : '비서에게 전화',
                    style: TextStyle(color: scheme.outline, fontSize: 12),
                  ),
                SizedBox(height: keyboard ? 6 : 8),
              ],
            ),
          ),
          Positioned(
            top: 0,
            right: 0,
            child: SafeArea(
              child: Padding(
                padding: const EdgeInsets.all(8),
                child: IconButton.filledTonal(
                  key: const Key('settings'),
                  onPressed: openSettings,
                  icon: const Icon(Icons.settings),
                  tooltip: '설정',
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// What the secretary is doing right now: status, the steps it is taking, and its answer as it forms.
class _ProgressCard extends StatelessWidget {
  const _ProgressCard({
    required this.status,
    required this.working,
    required this.steps,
    required this.liveText,
    this.detail,
    this.error,
    this.compact = false,
    this.rightInset = 0,
  });

  final double rightInset;

  final bool compact;
  final String status;
  final bool working;
  final List<Step> steps;
  final String liveText;
  final String? detail;
  final String? error;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final keep = compact ? 1 : 3;
    final recent = steps.length > keep
        ? steps.sublist(steps.length - keep)
        : steps;
    return Container(
      width: double.infinity,
      margin: EdgeInsets.fromLTRB(16, 12, 16 + rightInset, 8),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: Theme.of(context).cardColor,
        borderRadius: BorderRadius.circular(16),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              if (working)
                const Padding(
                  padding: EdgeInsets.only(right: 10),
                  child: SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  ),
                ),
              Expanded(
                child: Text(
                  status,
                  key: const Key('status'),
                  style: Theme.of(context).textTheme.titleMedium,
                ),
              ),
            ],
          ),
          if (detail != null)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(
                detail!,
                style: TextStyle(color: scheme.outline, fontSize: 12),
              ),
            ),
          for (final step in recent)
            Padding(
              padding: const EdgeInsets.only(top: 6),
              child: Row(
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
                        ? const Color(0xFF6FD9A4)
                        : scheme.outline,
                  ),
                  const SizedBox(width: 6),
                  Expanded(
                    child: Text(
                      step.label,
                      style: const TextStyle(fontSize: 13),
                    ),
                  ),
                ],
              ),
            ),
          if (working && !compact && liveText.trim().isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 8),
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
          if (error != null)
            Padding(
              padding: const EdgeInsets.only(top: 6),
              child: Text(error!, style: TextStyle(color: scheme.error)),
            ),
        ],
      ),
    );
  }
}

/// Work handed off to the fleet (DureClaw nodes), still visible after the conversation moves on.
class _TaskStrip extends StatelessWidget {
  const _TaskStrip({required this.tasks});

  final List<BackgroundTask> tasks;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 72,
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 16),
        itemCount: tasks.length,
        separatorBuilder: (_, _) => const SizedBox(width: 8),
        itemBuilder: (context, index) {
          final task = tasks[index];
          final failed = task.status == 'failed';
          return Container(
            width: 220,
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
            decoration: BoxDecoration(
              color: failed ? const Color(0xFF3A2430) : const Color(0xFF1B2E2A),
              borderRadius: BorderRadius.circular(12),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Text(
                  '${failed ? '실패' : '백그라운드 작업'} · ${task.to}',
                  style: const TextStyle(
                    fontSize: 11,
                    color: Color(0xFF9FB3C8),
                  ),
                ),
                Text(
                  task.summary,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontSize: 13),
                ),
              ],
            ),
          );
        },
      ),
    );
  }
}
