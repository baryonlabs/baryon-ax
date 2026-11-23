import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'face.dart';

const _channel = MethodChannel('baryon/voice');

/// One request to the secretary through the gateway's settings channel (board.get, goal.get, …).
Future<Map<String, dynamic>> secretaryRequest(
  Map<String, dynamic> request,
) async {
  try {
    final raw = await _channel.invokeMethod<String>('secretary', {
      'request': jsonEncode(request),
    });
    final answer = jsonDecode(raw ?? '{}');
    return answer is Map<String, dynamic> ? answer : {'ok': false};
  } on PlatformException catch (problem) {
    return {'ok': false, 'error': problem.message ?? problem.code};
  } on MissingPluginException {
    return {'ok': false, 'error': '앱에서만 쓸 수 있어요'};
  }
}

String ago(num? seconds) {
  if (seconds == null || seconds == 0) return '';
  final then = DateTime.fromMillisecondsSinceEpoch((seconds * 1000).round());
  final gap = DateTime.now().difference(then);
  if (gap.inMinutes < 1) return '방금';
  if (gap.inHours < 1) return '${gap.inMinutes}분 전';
  if (gap.inDays < 1) return '${gap.inHours}시간 전';
  return '${gap.inDays}일 전';
}

String when(num? seconds) {
  if (seconds == null) return '';
  final at = DateTime.fromMillisecondsSinceEpoch((seconds * 1000).round());
  final now = DateTime.now();
  final day = DateTime(
    at.year,
    at.month,
    at.day,
  ).difference(DateTime(now.year, now.month, now.day)).inDays;
  final time =
      '${at.hour.toString().padLeft(2, '0')}:${at.minute.toString().padLeft(2, '0')}';
  return switch (day) {
    0 => '오늘 $time',
    1 => '내일 $time',
    _ => '${at.month}/${at.day} $time',
  };
}

const starters = [
  ('오늘 일정 요약해 줘', Icons.event_note_outlined),
  ('읽지 않은 중요한 메일 알려 줘', Icons.mail_outline),
  ('열린 PR과 CI 상태 알려 줘', Icons.merge_type),
];

/// The app's home: what the secretary is doing, numbered the way it says them ("3번 작업이 끝났어요").
///
///   진행 중   goal agents working now (#26 추가 실험 …), with their latest step
///   완료      finished, failed or stopped goals
///   예약 알림 reminders and alarms waiting to ring
///   제안      questions a goal is waiting on, and things to ask
class TaskBoard extends StatefulWidget {
  const TaskBoard({super.key, required this.onAsk});

  /// Open the conversation with this request.
  final ValueChanged<String> onAsk;

  @override
  State<TaskBoard> createState() => _TaskBoardState();
}

class _TaskBoardState extends State<TaskBoard> {
  Map<String, dynamic> board = {};
  String? problem;
  bool loading = true;
  bool showAllClosed = false;
  final open = <String>{'progress', 'closed', 'reminders', 'suggestions'};
  Timer? poll;

  @override
  void initState() {
    super.initState();
    refresh();
    poll = Timer.periodic(const Duration(seconds: 10), (_) => refresh());
  }

  @override
  void dispose() {
    poll?.cancel();
    super.dispose();
  }

  Future<void> refresh() async {
    final answer = await secretaryRequest({'op': 'board.get'});
    if (!mounted) return;
    setState(() {
      loading = false;
      if (answer['ok'] == true && answer['data'] is Map) {
        board = Map<String, dynamic>.from(answer['data'] as Map);
        problem = null;
      } else {
        problem = answer['error'] as String? ?? '비서에 연결할 수 없어요';
      }
    });
  }

  List<Map<String, dynamic>> list(String key) => [
    for (final item in (board[key] as List? ?? const []))
      if (item is Map) Map<String, dynamic>.from(item),
  ];

  String get headline {
    final waiting = list('waiting');
    if (waiting.isNotEmpty) return '#${waiting.first['number']} 답을 기다려요';
    final progress = list('in_progress');
    if (progress.isNotEmpty) {
      final goal = progress.first;
      final step = goal['step'] as String? ?? '';
      return '#${goal['number']} ${step.isNotEmpty ? step : goal['title']}';
    }
    if (problem != null) return problem!;
    return '맡긴 일이 없어요 · 말로 맡겨 보세요';
  }

  Future<void> openGoal(Map<String, dynamic> goal) async {
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => GoalPage(goal: goal, onAsk: widget.onAsk),
      ),
    );
    refresh();
  }

  Future<void> openReminder(Map<String, dynamic> item) async {
    final cancel = await showModalBottomSheet<bool>(
      context: context,
      showDragHandle: true,
      builder: (context) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(20, 0, 20, 16),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                item['title'] as String? ?? '',
                style: Theme.of(context).textTheme.titleLarge,
              ),
              const SizedBox(height: 4),
              Text(
                '${item['alarm'] == true ? '알람' : '알림'} · ${when(item['at'] as num?)}',
              ),
              if ((item['text'] as String? ?? '').isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Text(item['text'] as String),
                ),
              const SizedBox(height: 16),
              FilledButton.tonalIcon(
                key: const Key('cancel-reminder'),
                onPressed: () => Navigator.pop(context, true),
                icon: const Icon(Icons.notifications_off_outlined),
                label: const Text('이 알림 취소'),
              ),
            ],
          ),
        ),
      ),
    );
    if (cancel == true) {
      await secretaryRequest({'op': 'notify.cancel', 'target': item['id']});
      refresh();
    }
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final progress = list('in_progress');
    final closed = list('closed');
    final reminders = list('reminders');
    final waiting = list('waiting');
    final shownClosed = showAllClosed ? closed : closed.take(5).toList();
    return SafeArea(
      child: RefreshIndicator(
        onRefresh: refresh,
        child: ListView(
          key: const Key('board'),
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
          children: [
            Row(
              children: [
                FloatingFace(
                  size: 64,
                  state: FaceState(
                    mood: waiting.isNotEmpty
                        ? FaceMood.question
                        : progress.isNotEmpty
                        ? FaceMood.thinking
                        : FaceMood.idle,
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        '바리온 비서',
                        style: Theme.of(context).textTheme.titleLarge?.copyWith(
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                      Text(
                        loading ? '불러오는 중…' : headline,
                        key: const Key('headline'),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(color: scheme.onSurfaceVariant),
                      ),
                    ],
                  ),
                ),
                IconButton(
                  onPressed: refresh,
                  icon: const Icon(Icons.refresh),
                  tooltip: '새로 고침',
                ),
              ],
            ),
            if (problem != null && board.isEmpty)
              Card(
                margin: const EdgeInsets.only(top: 12),
                child: ListTile(
                  leading: Icon(Icons.cloud_off, color: scheme.error),
                  title: const Text('비서 목록을 불러오지 못했어요'),
                  subtitle: Text(problem!),
                  trailing: TextButton(
                    onPressed: refresh,
                    child: const Text('다시'),
                  ),
                ),
              ),
            _Section(
              id: 'progress',
              title: '진행 중',
              count: progress.length,
              open: open,
              onToggle: () => setState(() {}),
              children: [
                if (progress.isEmpty) const _Empty('지금 일하는 목표 에이전트가 없어요'),
                for (final goal in progress)
                  _GoalRow(goal: goal, onTap: () => openGoal(goal)),
              ],
            ),
            _Section(
              id: 'closed',
              title: '완료',
              count: closed.length,
              open: open,
              onToggle: () => setState(() {}),
              children: [
                if (closed.isEmpty) const _Empty('아직 끝난 일이 없어요'),
                for (final goal in shownClosed)
                  _GoalRow(goal: goal, onTap: () => openGoal(goal)),
                if (closed.length > 5)
                  TextButton(
                    key: const Key('more-closed'),
                    onPressed: () =>
                        setState(() => showAllClosed = !showAllClosed),
                    child: Text(
                      showAllClosed ? '접기' : '${closed.length - 5}개 더 보기',
                    ),
                  ),
              ],
            ),
            _Section(
              id: 'reminders',
              title: '예약 알림',
              count: reminders.length,
              open: open,
              onToggle: () => setState(() {}),
              children: [
                if (reminders.isEmpty) const _Empty('"7시에 깨워 줘"처럼 말하면 여기 생겨요'),
                for (final item in reminders)
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    dense: true,
                    leading: Icon(
                      item['alarm'] == true
                          ? Icons.alarm
                          : Icons.notifications_none,
                      color: item['alarm'] == true
                          ? scheme.error
                          : scheme.primary,
                    ),
                    title: Text(
                      item['title'] as String? ?? '',
                      style: const TextStyle(fontSize: 16),
                    ),
                    subtitle: Text(when(item['at'] as num?)),
                    onTap: () => openReminder(item),
                  ),
              ],
            ),
            _Section(
              id: 'suggestions',
              title: '제안',
              count: waiting.length + starters.length,
              open: open,
              onToggle: () => setState(() {}),
              children: [
                for (final goal in waiting)
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    dense: true,
                    leading: Icon(Icons.help_outline, color: scheme.tertiary),
                    title: Text(
                      '#${goal['number']}에 답하기',
                      style: const TextStyle(fontSize: 16),
                    ),
                    subtitle: Text(
                      goal['question'] as String? ?? '',
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                    onTap: () => openGoal(goal),
                  ),
                for (final (text, icon) in starters)
                  ListTile(
                    key: Key('starter-$text'),
                    contentPadding: EdgeInsets.zero,
                    dense: true,
                    leading: Icon(icon, color: scheme.onSurfaceVariant),
                    title: Text(text, style: const TextStyle(fontSize: 16)),
                    onTap: () => widget.onAsk(text),
                  ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _Section extends StatelessWidget {
  const _Section({
    required this.id,
    required this.title,
    required this.count,
    required this.open,
    required this.onToggle,
    required this.children,
  });

  final String id;
  final String title;
  final int count;
  final Set<String> open;
  final VoidCallback onToggle;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final expanded = open.contains(id);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        InkWell(
          key: Key('section-$id'),
          onTap: () {
            expanded ? open.remove(id) : open.add(id);
            onToggle();
          },
          child: Padding(
            padding: const EdgeInsets.only(top: 18, bottom: 4),
            child: Row(
              children: [
                Icon(
                  expanded ? Icons.expand_more : Icons.chevron_right,
                  size: 20,
                  color: scheme.onSurfaceVariant,
                ),
                const SizedBox(width: 4),
                Text(
                  title,
                  style: TextStyle(
                    color: scheme.onSurfaceVariant,
                    fontWeight: FontWeight.w700,
                    fontSize: 15,
                  ),
                ),
                if (count > 0) ...[
                  const SizedBox(width: 6),
                  Text(
                    '$count',
                    style: TextStyle(color: scheme.outline, fontSize: 13),
                  ),
                ],
              ],
            ),
          ),
        ),
        if (expanded) ...children,
      ],
    );
  }
}

class _Empty extends StatelessWidget {
  const _Empty(this.text);
  final String text;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.fromLTRB(28, 6, 0, 6),
    child: Text(
      text,
      style: TextStyle(color: Theme.of(context).colorScheme.outline),
    ),
  );
}

/// The status mark, as in a task list: a ring while working, a check when done.
Widget statusIcon(BuildContext context, String status) {
  final scheme = Theme.of(context).colorScheme;
  return switch (status) {
    'running' => const Icon(
      Icons.radio_button_unchecked,
      color: Color(0xFFE5B21C),
    ),
    'queued' => Icon(Icons.radio_button_unchecked, color: scheme.outline),
    'needs_input' => Icon(Icons.help, color: scheme.tertiary),
    'done' => const Icon(Icons.check_circle, color: Color(0xFF6FCF8A)),
    'failed' => Icon(Icons.cancel, color: scheme.error),
    'interrupted' => const Icon(Icons.pause_circle, color: Color(0xFFE5A04B)),
    _ => Icon(Icons.block, color: scheme.outline),
  };
}

String statusLabel(String status) => switch (status) {
  'running' => '진행 중',
  'queued' => '대기',
  'needs_input' => '답을 기다림',
  'done' => '완료',
  'failed' => '실패',
  'interrupted' => '중단됨',
  'cancelled' => '멈춤',
  _ => status,
};

class _GoalRow extends StatelessWidget {
  const _GoalRow({required this.goal, required this.onTap});
  final Map<String, dynamic> goal;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final status = goal['status'] as String? ?? '';
    final active =
        status == 'running' || status == 'queued' || status == 'needs_input';
    final step = goal['step'] as String? ?? '';
    final detail = status == 'needs_input'
        ? goal['question'] as String? ?? ''
        : active
        ? step
        : ago(goal['updated'] as num?);
    return InkWell(
      key: Key('goal-${goal['number']}'),
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 7),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            statusIcon(context, status),
            const SizedBox(width: 12),
            SizedBox(
              width: 40,
              child: Text(
                '#${goal['number']}',
                style: TextStyle(color: scheme.outline, fontSize: 16),
              ),
            ),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    goal['title'] as String? ?? '',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      fontSize: 16,
                      color: active
                          ? scheme.onSurface
                          : scheme.onSurfaceVariant,
                    ),
                  ),
                  if (detail.isNotEmpty)
                    Text(
                      detail,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(fontSize: 13, color: scheme.outline),
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

/// One goal: what was asked, what the agent did, its question or its report.
class GoalPage extends StatefulWidget {
  const GoalPage({super.key, required this.goal, required this.onAsk});
  final Map<String, dynamic> goal;
  final ValueChanged<String> onAsk;

  @override
  State<GoalPage> createState() => _GoalPageState();
}

class _GoalPageState extends State<GoalPage> {
  late Map<String, dynamic> goal = widget.goal;
  final answer = TextEditingController();
  String? note;

  @override
  void initState() {
    super.initState();
    load();
  }

  Future<void> load() async {
    final result = await secretaryRequest({
      'op': 'goal.get',
      'target': goal['id'],
    });
    if (!mounted || result['ok'] != true || result['data'] is! Map) return;
    setState(() => goal = Map<String, dynamic>.from(result['data'] as Map));
  }

  Future<void> sendAnswer() async {
    final text = answer.text.trim();
    if (text.isEmpty) return;
    final result = await secretaryRequest({
      'op': 'goal.answer',
      'target': goal['id'],
      'text': text,
    });
    if (!mounted) return;
    final ok = result['ok'] == true && (result['data'] as Map?)?['ok'] == true;
    setState(
      () => note = ok
          ? '전달했어요. 에이전트가 이어서 일해요.'
          : '전달하지 못했어요. 이미 다른 답을 받았을 수 있어요.',
    );
    if (ok) answer.clear();
    load();
  }

  Future<void> stop() async {
    final sure = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('#${goal['number']} 작업을 멈출까요?'),
        content: const Text('에이전트가 하던 일을 멈춥니다. 커밋한 내용은 브랜치에 남아요.'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('계속하기'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('멈추기'),
          ),
        ],
      ),
    );
    if (sure != true) return;
    await secretaryRequest({'op': 'goal.cancel', 'target': goal['id']});
    load();
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;
    final status = goal['status'] as String? ?? '';
    final active =
        status == 'running' || status == 'queued' || status == 'needs_input';
    final steps = [
      for (final step in (goal['steps'] as List? ?? const [])) '$step',
    ];
    Widget label(String value) => Padding(
      padding: const EdgeInsets.only(top: 18, bottom: 6),
      child: Text(
        value,
        style: text.labelLarge?.copyWith(color: scheme.onSurfaceVariant),
      ),
    );
    return Scaffold(
      appBar: AppBar(
        title: Text('#${goal['number']}'),
        actions: [
          if (active)
            TextButton.icon(
              key: const Key('stop-goal'),
              onPressed: stop,
              icon: const Icon(Icons.stop_circle_outlined),
              label: const Text('멈추기'),
            ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: load,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(20, 4, 20, 32),
          children: [
            Text(
              goal['title'] as String? ?? '',
              style: text.headlineSmall?.copyWith(fontWeight: FontWeight.w700),
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                statusIcon(context, status),
                const SizedBox(width: 6),
                Text(statusLabel(status)),
                const SizedBox(width: 10),
                Text(
                  ago(goal['updated'] as num?),
                  style: TextStyle(color: scheme.outline),
                ),
              ],
            ),
            if (status == 'needs_input') ...[
              label('에이전트가 묻는 것'),
              Container(
                padding: const EdgeInsets.all(14),
                decoration: BoxDecoration(
                  color: scheme.tertiaryContainer,
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Text(
                  goal['question'] as String? ?? '',
                  style: TextStyle(
                    color: scheme.onTertiaryContainer,
                    fontSize: 16,
                  ),
                ),
              ),
              const SizedBox(height: 8),
              Row(
                children: [
                  Expanded(
                    child: TextField(
                      key: const Key('goal-answer'),
                      controller: answer,
                      decoration: const InputDecoration(
                        hintText: '답 입력',
                        border: OutlineInputBorder(),
                      ),
                      onSubmitted: (_) => sendAnswer(),
                    ),
                  ),
                  const SizedBox(width: 8),
                  IconButton.filled(
                    key: const Key('send-answer'),
                    onPressed: sendAnswer,
                    icon: const Icon(Icons.send),
                  ),
                ],
              ),
            ],
            if (note != null)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(note!),
              ),
            if ((goal['summary'] as String? ?? '').isNotEmpty) ...[
              label('보고'),
              SelectableText(
                goal['summary'] as String,
                style: const TextStyle(fontSize: 16, height: 1.45),
              ),
            ],
            if ((goal['goal'] as String? ?? '').isNotEmpty) ...[
              label('맡긴 일'),
              Text(goal['goal'] as String, style: const TextStyle(height: 1.4)),
            ],
            if ((goal['done_when'] as String? ?? '').isNotEmpty) ...[
              label('완료 조건'),
              Text(
                goal['done_when'] as String,
                style: const TextStyle(height: 1.4),
              ),
            ],
            if (steps.isNotEmpty) ...[
              label('진행 기록'),
              for (var i = 0; i < steps.length; i++)
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Column(
                      children: [
                        Icon(
                          i == steps.length - 1 && active
                              ? Icons.radio_button_checked
                              : Icons.circle,
                          size: i == steps.length - 1 && active ? 14 : 8,
                          color: i == steps.length - 1 && active
                              ? const Color(0xFFE5B21C)
                              : scheme.outline,
                        ),
                      ],
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Padding(
                        padding: const EdgeInsets.only(bottom: 8),
                        child: Text(steps[i]),
                      ),
                    ),
                  ],
                ),
            ],
            if ((goal['branch'] as String? ?? '').isNotEmpty) ...[
              label('브랜치'),
              SelectableText(
                goal['branch'] as String,
                style: const TextStyle(fontFamily: 'monospace'),
              ),
            ],
            const SizedBox(height: 20),
            OutlinedButton.icon(
              key: const Key('ask-about-goal'),
              onPressed: () {
                Navigator.of(context).pop();
                widget.onAsk('${goal['number']}번 작업 어떻게 됐어?');
              },
              icon: const Icon(Icons.forum_outlined),
              label: Text('비서에게 #${goal['number']} 물어보기'),
            ),
          ],
        ),
      ),
    );
  }
}
