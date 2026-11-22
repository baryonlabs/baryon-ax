import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

const _channel = MethodChannel('baryon/voice');

/// 설정 > 알림: the secretary's own notifications (no FCM). Shows whether the phone is connected and
/// whether Android lets the alarm ring and wake the screen, with a button for each thing to fix, and
/// sends a real test notification through the secretary.
class AlertsSettings extends StatefulWidget {
  const AlertsSettings({super.key});

  @override
  State<AlertsSettings> createState() => _AlertsSettingsState();
}

class _AlertsSettingsState extends State<AlertsSettings>
    with WidgetsBindingObserver {
  Map<String, dynamic> state = {};
  String? message;
  Timer? poll;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    refresh();
  }

  @override
  void dispose() {
    poll?.cancel();
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState lifecycle) {
    // Back from a system settings screen: see what the owner allowed.
    if (lifecycle == AppLifecycleState.resumed) {
      refresh();
    }
  }

  Future<void> refresh() async {
    try {
      final next = await _channel.invokeMethod<Object?>('alertsState');
      if (next is! Map || !mounted) return;
      setState(() => state = Map<String, dynamic>.from(next));
      // While it connects, look again shortly; once connected there is nothing to watch.
      poll?.cancel();
      if (state['on'] == true && state['connected'] != true) {
        poll = Timer(const Duration(seconds: 2), refresh);
      }
    } on PlatformException {
      // not on Android (tests, desktop)
    } on MissingPluginException {
      // ditto
    }
  }

  Future<void> test({required bool alarm}) async {
    setState(() => message = '보내는 중…');
    final raw = await _channel.invokeMethod<String>('secretary', {
      'request': jsonEncode({
        'op': 'notify.add',
        'title': alarm ? '알람 테스트' : '알림 테스트',
        'text': alarm ? '화면이 켜지고 끌 때까지 울려요.' : '비서 알림이 이렇게 와요.',
        'when': alarm
            ? '0.25'
            : null, // 15 seconds: time to turn the screen off and see it wake
        'alarm': alarm,
      }),
    });
    final answer = jsonDecode(raw ?? '{}') as Map<String, dynamic>;
    if (!mounted) return;
    setState(
      () => message = answer['ok'] == true
          ? (alarm ? '15초 뒤에 울려요. 화면을 끄고 기다려 보세요.' : '보냈어요. 곧 울려요.')
          : '보내지 못했어요: ${answer['error'] ?? '알 수 없는 오류'}',
    );
  }

  @override
  Widget build(BuildContext context) {
    final on = state['on'] == true;
    final small = Theme.of(context).textTheme.bodySmall;
    Widget fix(String key, String label, String what) => state[key] == false
        ? ListTile(
            key: Key('fix-$what'),
            contentPadding: EdgeInsets.zero,
            dense: true,
            leading: const Icon(
              Icons.warning_amber_rounded,
              color: Colors.orange,
            ),
            title: Text(label),
            trailing: TextButton(
              onPressed: () =>
                  _channel.invokeMethod('fixAlerts', {'what': what}),
              child: const Text('허용하기'),
            ),
          )
        : const SizedBox.shrink();

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SwitchListTile(
          key: const Key('alerts'),
          contentPadding: EdgeInsets.zero,
          title: const Text('비서 알림 받기'),
          subtitle: Text(
            on
                ? (state['connected'] == true
                      ? '연결됨 · 화면이 꺼져 있어도 울려요'
                      : '${state['status'] ?? '연결하는 중'}')
                : '꺼짐 · 예약 알림과 알람을 받지 않아요',
          ),
          value: on,
          onChanged: (value) async {
            await _channel.invokeMethod('alerts', {'on': value});
            await refresh();
          },
        ),
        if (on) ...[
          fix('notificationsAllowed', '알림이 꺼져 있어요', 'notifications'),
          fix(
            'fullScreenAllowed',
            '알람이 화면을 켜려면 "전체 화면 알림" 허용이 필요해요',
            'fullScreen',
          ),
          fix('batteryExempt', '잠든 동안에도 연결을 유지하려면 배터리 최적화 제외가 필요해요', 'battery'),
          Wrap(
            spacing: 8,
            children: [
              OutlinedButton.icon(
                key: const Key('test-alert'),
                onPressed: () => test(alarm: false),
                icon: const Icon(Icons.notifications_active_outlined),
                label: const Text('알림 테스트'),
              ),
              OutlinedButton.icon(
                key: const Key('test-alarm'),
                onPressed: () => test(alarm: true),
                icon: const Icon(Icons.alarm),
                label: const Text('알람 테스트 (15초 뒤)'),
              ),
            ],
          ),
          Padding(
            padding: const EdgeInsets.only(top: 6),
            child: Text(
              message ?? '비서에게 "내일 7시에 깨워 줘", "3시에 회의 알려 줘"라고 말하면 여기로 와요.',
              style: small,
            ),
          ),
        ],
      ],
    );
  }
}
