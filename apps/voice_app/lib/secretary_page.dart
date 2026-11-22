import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

const _channel = MethodChannel('baryon/voice');

/// "비서에게 알려 주기": what the secretary knows about its owner, kept on the server
/// (apps/secretary/secretary/profile.py) and read at the start of every call.
///
/// - 나에 대해: who I am (자기 소개)
/// - 이렇게 써 줘: how I want it to work for me (활용)
/// - 기억: short facts, also added by voice ("이거 기억해 줘")
/// - 연결: which MCP connections it may use
class SecretaryPage extends StatefulWidget {
  const SecretaryPage({super.key});

  @override
  State<SecretaryPage> createState() => _SecretaryPageState();
}

class _SecretaryPageState extends State<SecretaryPage> {
  final about = TextEditingController();
  final usage = TextEditingController();
  final memory = TextEditingController();
  List<Map<String, dynamic>> memories = [];
  List<Map<String, dynamic>> connections = [];
  bool loading = true;
  bool saving = false;
  bool dirty = false;
  String? error;

  @override
  void initState() {
    super.initState();
    load();
  }

  @override
  void dispose() {
    about.dispose();
    usage.dispose();
    memory.dispose();
    super.dispose();
  }

  Future<Map<String, dynamic>?> ask(Map<String, dynamic> request) async {
    final raw = await _channel.invokeMethod<String>('secretary', {
      'request': jsonEncode(request),
    });
    final answer = jsonDecode(raw ?? '{}') as Map<String, dynamic>;
    if (answer['ok'] != true) {
      if (mounted) {
        setState(() => error = answer['error'] as String? ?? '요청이 실패했어요');
      }
      return null;
    }
    if (mounted && error != null) setState(() => error = null);
    return answer['data'] as Map<String, dynamic>? ?? {};
  }

  void show(Map<String, dynamic> profile) {
    about.text = profile['about'] as String? ?? '';
    usage.text = profile['usage'] as String? ?? '';
    memories = [
      for (final item in profile['memories'] as List? ?? const [])
        Map<String, dynamic>.from(item as Map),
    ];
    connections = [
      for (final item in profile['mcp'] as List? ?? const [])
        Map<String, dynamic>.from(item as Map),
    ];
    dirty = false;
  }

  Future<void> load() async {
    setState(() => loading = true);
    final profile = await ask({'op': 'profile.get'});
    if (!mounted) return;
    setState(() {
      loading = false;
      if (profile != null) show(profile);
    });
  }

  Future<void> saveTexts() async {
    setState(() => saving = true);
    final profile = await ask({
      'op': 'profile.put',
      'about': about.text,
      'usage': usage.text,
    });
    if (!mounted) return;
    setState(() {
      saving = false;
      if (profile != null) {
        dirty = false;
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(const SnackBar(content: Text('저장했어요. 다음 통화부터 반영돼요.')));
      }
    });
  }

  Future<void> addMemory() async {
    final text = memory.text.trim();
    if (text.isEmpty) return;
    final added = await ask({'op': 'memory.add', 'text': text});
    if (added == null || !mounted) return;
    setState(() {
      memories.insert(0, added);
      memory.clear();
    });
  }

  Future<void> forget(Map<String, dynamic> item) async {
    final done = await ask({'op': 'memory.delete', 'memory': item['id']});
    if (done == null || !mounted) return;
    setState(() => memories.remove(item));
  }

  Future<void> toggle(Map<String, dynamic> connection, bool on) async {
    setState(() => connection['enabled'] = on);
    final enabled = [
      for (final item in connections)
        if (item['enabled'] == true) item['name'],
    ];
    final profile = await ask({'op': 'profile.put', 'mcp': enabled});
    if (!mounted) return;
    if (profile == null) setState(() => connection['enabled'] = !on);
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final small = Theme.of(context).textTheme.bodySmall;
    Widget section(String title, [String? note]) => Padding(
      padding: const EdgeInsets.only(top: 24, bottom: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            title,
            style: Theme.of(
              context,
            ).textTheme.titleMedium?.copyWith(color: scheme.primary),
          ),
          if (note != null) Text(note, style: small),
        ],
      ),
    );
    InputDecoration field(String hint) => InputDecoration(
      hintText: hint,
      hintMaxLines: 4,
      filled: true,
      fillColor: scheme.surfaceContainerHigh,
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(16),
        borderSide: BorderSide.none,
      ),
    );

    return Scaffold(
      appBar: AppBar(title: const Text('비서에게 알려 주기')),
      body: loading
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: load,
              child: ListView(
                key: const Key('secretary-page'),
                padding: const EdgeInsets.fromLTRB(16, 0, 16, 32),
                children: [
                  if (error != null)
                    Card(
                      color: scheme.errorContainer,
                      child: ListTile(
                        leading: const Icon(Icons.cloud_off),
                        title: Text(error!),
                        subtitle: const Text('설정의 서버 주소와 기기 토큰을 확인해 주세요'),
                        trailing: IconButton(
                          onPressed: load,
                          icon: const Icon(Icons.refresh),
                          tooltip: '다시 시도',
                        ),
                      ),
                    ),
                  const _Intro(),
                  section('나에 대해', '비서가 나를 알고 대화하도록. 이름·호칭, 하는 일, 진행 중인 일'),
                  TextField(
                    key: const Key('about'),
                    controller: about,
                    minLines: 3,
                    maxLines: 8,
                    maxLength: 2000,
                    onChanged: (_) => setState(() => dirty = true),
                    decoration: field(
                      '예: 스타트업 대표 홍길동. 사내 AI 플랫폼과 음성 비서를 만들고 있어. '
                      '주로 회사 메인 저장소에서 일해.',
                    ),
                  ),
                  section('이렇게 써 줘', '말투, 답의 길이, 먼저 챙길 것'),
                  TextField(
                    key: const Key('usage'),
                    controller: usage,
                    minLines: 3,
                    maxLines: 8,
                    maxLength: 2000,
                    onChanged: (_) => setState(() => dirty = true),
                    decoration: field(
                      '예: 운전 중이 많으니 답은 두 문장으로. 아침 첫 통화엔 PR과 CI부터. '
                      '코딩은 목표 에이전트에게 맡기고 끝나면 알려 줘.',
                    ),
                  ),
                  Align(
                    alignment: Alignment.centerRight,
                    child: FilledButton.icon(
                      key: const Key('save-profile'),
                      onPressed: dirty && !saving ? saveTexts : null,
                      icon: const Icon(Icons.save),
                      label: Text(saving ? '저장 중' : '저장'),
                    ),
                  ),
                  section('기억', '통화 중에 "이거 기억해 줘"라고 말해도 여기에 쌓여요'),
                  Row(
                    children: [
                      Expanded(
                        child: TextField(
                          key: const Key('memory-input'),
                          controller: memory,
                          maxLength: 300,
                          textInputAction: TextInputAction.done,
                          onSubmitted: (_) => addMemory(),
                          decoration: field('예: 회의록은 노션 "회의" 페이지에 둬'),
                        ),
                      ),
                      const SizedBox(width: 8),
                      IconButton.filled(
                        key: const Key('memory-add'),
                        onPressed: addMemory,
                        icon: const Icon(Icons.add),
                        tooltip: '기억에 추가',
                      ),
                    ],
                  ),
                  if (memories.isEmpty)
                    Padding(
                      padding: const EdgeInsets.symmetric(vertical: 8),
                      child: Text('아직 기억해 둔 것이 없어요.', style: small),
                    ),
                  for (final item in memories)
                    ListTile(
                      contentPadding: EdgeInsets.zero,
                      leading: Icon(
                        item['source'] == 'voice'
                            ? Icons.record_voice_over
                            : Icons.edit_note,
                      ),
                      title: Text(item['text'] as String? ?? ''),
                      subtitle: Text(
                        item['source'] == 'voice' ? '말로 기억함' : '앱에서 적음',
                        style: small,
                      ),
                      trailing: IconButton(
                        onPressed: () => forget(item),
                        icon: const Icon(Icons.delete_outline),
                        tooltip: '잊기',
                      ),
                    ),
                  section(
                    '연결 (MCP)',
                    '비서가 쓸 도구. 작업용 PC의 Claude Code에 설정된 것이 보여요. 다음 통화부터 적용',
                  ),
                  if (connections.isEmpty)
                    Text('작업용 PC에 설정된 MCP 연결이 없어요.', style: small),
                  for (final connection in connections)
                    SwitchListTile(
                      key: Key('mcp-${connection['name']}'),
                      contentPadding: EdgeInsets.zero,
                      title: Text(connection['name'] as String? ?? ''),
                      subtitle: Text(
                        connection['installed'] == false
                            ? 'PC 설정에서 사라짐'
                            : switch (connection['kind']) {
                                'stdio' => 'PC에서 실행',
                                'http' || 'sse' => '원격 서버',
                                _ => '기타',
                              },
                        style: small,
                      ),
                      value: connection['enabled'] == true,
                      onChanged: (on) => toggle(connection, on),
                    ),
                ],
              ),
            ),
    );
  }
}

/// 비서 소개: what it is and how to use it, so the owner knows what to ask.
class _Intro extends StatelessWidget {
  const _Intro();

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    const uses = [
      (Icons.summarize, '요약', '"메일이랑 일정 알려줘", "PR이랑 CI 상태는?"'),
      (Icons.flag, '목표 맡기기', '"로그인 버그 고쳐서 테스트 통과시켜 줘" → 끝나면 먼저 알려 줘요'),
      (Icons.lan, '다른 컴퓨터', '"윈도우에서 빌드해 줘" → DureClaw 노드로 보내요'),
      (Icons.lightbulb, '아이디어', '말로 던지면 정리해 되읽고, 원하면 할 일로 저장해요'),
    ];
    return Card(
      color: scheme.secondaryContainer,
      margin: const EdgeInsets.only(top: 8),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              '바리온 비서는 전화로 일을 받는 내 비서예요',
              style: Theme.of(context).textTheme.titleSmall,
            ),
            const SizedBox(height: 4),
            Text(
              '바뀌는 일은 "응"이라고 해야 실행하고, 말하는 중에 끼어들면 바로 멈춰요. 음성은 사내 서버에서만 처리해요.',
              style: Theme.of(context).textTheme.bodySmall,
            ),
            for (final (icon, title, example) in uses)
              Padding(
                padding: const EdgeInsets.only(top: 10),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Icon(icon, size: 20, color: scheme.onSecondaryContainer),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text.rich(
                        TextSpan(
                          children: [
                            TextSpan(
                              text: '$title  ',
                              style: const TextStyle(
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                            TextSpan(text: example),
                          ],
                        ),
                      ),
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
