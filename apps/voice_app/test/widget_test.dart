import 'dart:convert';

import 'package:baryon_voice/cards.dart';
import 'package:baryon_voice/main.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  final calls = <MethodCall>[];
  var alertsOn = true;

  setUp(() {
    calls.clear();
    alertsOn = true;
    SharedPreferences.setMockInitialValues({});
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(const MethodChannel('baryon/voice'), (
          call,
        ) async {
          calls.add(call);
          if (call.method == 'secretary') {
            final request =
                jsonDecode(call.arguments['request'] as String) as Map;
            final goal = {
              'id': 'a1b2c3',
              'number': 26,
              'title': '추가 실험: 외부 참여자 파일럿',
              'status': 'needs_input',
              'step': '설문 초안 작성',
              'question': '참여자는 몇 명으로 할까요?',
              'updated': 0,
            };
            if (request['op'] == 'board.get') {
              return jsonEncode({
                'id': 1,
                'ok': true,
                'data': {
                  'in_progress': [
                    goal,
                    {
                      'id': 'b',
                      'number': 15,
                      'title': '논문 점수 상한 돌파',
                      'status': 'running',
                      'step': '명령: 테스트 실행',
                    },
                  ],
                  'closed': [
                    for (var n = 20; n < 28; n++)
                      {
                        'id': 'c$n',
                        'number': n + 10,
                        'title': '끝난 일 $n',
                        'status': n == 21 ? 'failed' : 'done',
                      },
                  ],
                  'reminders': [
                    {
                      'id': 'r1',
                      'title': '회의 알림',
                      'alarm': false,
                      'at': 4102444800,
                    },
                  ],
                  'waiting': [goal],
                },
              });
            }
            if (request['op'] == 'goal.get') {
              return jsonEncode({
                'id': 1,
                'ok': true,
                'data': {
                  ...goal,
                  'goal': '외부 참여자로 파일럿을 돌린다',
                  'done_when': '결과 표',
                  'steps': ['계획', '설문 초안 작성'],
                },
              });
            }
            if (request['op'] == 'goal.answer') {
              return jsonEncode({
                'id': 1,
                'ok': true,
                'data': {'ok': true},
              });
            }
            final data = switch (request['op']) {
              'memory.add' => {
                'id': 'b1',
                'text': request['text'],
                'source': 'app',
              },
              _ => {
                'about': '음성 비서를 만드는 사람',
                'usage': '',
                'memories': [
                  {'id': 'a1', 'text': '아침엔 PR부터', 'source': 'voice'},
                ],
                'brain': 'default',
                'goal_brain': 'deep',
                'brains': [
                  {'id': 'fast', 'label': '빠르게'},
                  {'id': 'smart', 'label': '똑똑하게'},
                  {'id': 'best', 'label': '최고'},
                ],
                'goal_brains': [
                  {'id': 'standard', 'label': '기본'},
                  {'id': 'deep', 'label': '깊게'},
                  {'id': 'best', 'label': '최고'},
                ],
                'mcp': [
                  {
                    'name': 'kb-baryon',
                    'kind': 'stdio',
                    'enabled': true,
                    'installed': true,
                  },
                ],
              },
            };
            return jsonEncode({'id': 1, 'ok': true, 'data': data});
          }
          if (call.method == 'alertsState') {
            return {
              'on': alertsOn,
              'connected': true,
              'status': '알림 기다리는 중',
              'notificationsAllowed': true,
              'fullScreenAllowed': false,
              'batteryExempt': true,
            };
          }
          if (call.method == 'alerts') alertsOn = call.arguments['on'] as bool;
          if (call.method == 'recordStart') return null; // started
          if (call.method == 'recordState') {
            return {'recording': false, 'seconds': 0.0, 'lastPath': null};
          }
          return call.method == 'config'
              ? {
                  'server': 'ws://127.0.0.1:9093/v1/voice/call',
                  'hasToken': true,
                  'inCall': false,
                }
              : true;
        });
  });

  Future<void> emit(WidgetTester tester, Map<String, dynamic> event) async {
    await TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .handlePlatformMessage(
          'baryon/voice/events',
          const StandardMethodCodec().encodeSuccessEnvelope(jsonEncode(event)),
          (_) {},
        );
    await tester.pump();
  }

  Iterable<String> methods() => calls.map((call) => call.method);

  Future<void> open(WidgetTester tester) async {
    await tester.pumpWidget(const BaryonVoiceApp(startInChat: true));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 50));
  }

  testWidgets(
    'the conversation fills the screen; status is a chip with a moving ear',
    (tester) async {
      await open(tester);
      expect(find.byType(AppBar), findsNothing);
      await tester.tap(find.byKey(const Key('call')));
      await tester.pump();
      expect(methods(), contains('start'));
      expect(methods(), contains('callPrefs'));

      await emit(tester, {'type': 'state', 'state': 'connected'});
      expect(find.byKey(const Key('ear')), findsOneWidget);
      await emit(tester, {'type': 'vad', 'speaking': true});
      await emit(tester, {'type': 'heard', 'text': '로그인 버그 빌더한테 맡겨'});
      await emit(tester, {
        'type': 'activity',
        'id': 'a1',
        'label': '플릿 상태를 보고 있어요.',
      });
      await emit(tester, {'type': 'agent_text', 'delta': '빌더에게 맡길게요.'});
      expect(find.byKey(const Key('working')), findsOneWidget);
      expect(find.text('빌더에게 맡길게요.'), findsOneWidget);

      await emit(tester, {
        'type': 'task',
        'id': 'g1',
        'to': '목표 에이전트',
        'summary': '로그인 버그',
        'status': 'running',
      });
      expect(find.text('로그인 버그'), findsOneWidget);
      await emit(tester, {'type': 'task', 'id': 'g1', 'status': 'done'});
      expect(find.byIcon(Icons.check_circle), findsWidgets);

      await emit(tester, {'type': 'said', 'text': '빌더에게 맡겼어요.'});
      await emit(tester, {'type': 'agent_done'});
      expect(find.byKey(const Key('working')), findsNothing);
      expect(find.text('빌더에게 맡겼어요.'), findsOneWidget);

      await tester.tap(find.byIcon(Icons.volume_up));
      await tester.pump();
      expect(calls.where((call) => call.method == 'speaker').single.arguments, {
        'on': true,
      });
      await emit(tester, {'type': 'ignored', 'reason': 'quiet'});
    },
  );

  testWidgets(
    'typing works without a call, dictation fills the box, history survives a restart',
    (tester) async {
      await open(tester);
      await tester.enterText(find.byKey(const Key('input')), '오늘 일정 알려줘');
      await tester.tap(find.byKey(const Key('send')));
      await tester.pump();
      expect(calls.where((call) => call.method == 'text').single.arguments, {
        'text': '오늘 일정 알려줘',
      });

      await tester.tap(find.byKey(const Key('dictate')));
      await tester.pump();
      await emit(tester, {'type': 'dictation', 'text': '메일도 정리해줘'});
      expect(find.widgetWithText(TextField, '메일도 정리해줘'), findsOneWidget);

      await tester.pump(
        const Duration(seconds: 1),
      ); // history is saved after a short pause
      final saved =
          (await SharedPreferences.getInstance()).getString('history') ?? '';
      expect(saved, contains('오늘 일정 알려줘'));

      await tester.pumpWidget(const SizedBox());
      await open(tester);
      await tester.pump(const Duration(milliseconds: 100));
      expect(find.text('오늘 일정 알려줘'), findsOneWidget);
    },
  );

  testWidgets(
    'a new call starts a new answer bubble; distant voices are counted',
    (tester) async {
      await open(tester);
      await emit(tester, {'type': 'state', 'state': 'connected'});
      await emit(tester, {'type': 'said', 'text': '첫 문장.'});
      await emit(tester, {'type': 'said', 'text': '둘째 문장.'});
      expect(find.text('첫 문장. 둘째 문장.'), findsOneWidget);
      await emit(tester, {'type': 'idle'});
      await emit(tester, {'type': 'state', 'state': 'ended'});
      await emit(tester, {'type': 'state', 'state': 'connected'});
      await emit(tester, {'type': 'said', 'text': '네, 비서입니다.'});
      expect(find.text('네, 비서입니다.'), findsOneWidget);
      await emit(tester, {'type': 'ignored', 'reason': 'distant'});
      await emit(tester, {'type': 'state', 'state': 'ended'});
    },
  );

  testWidgets('a confirmation can be answered by tapping', (tester) async {
    await open(tester);
    await emit(tester, {'type': 'state', 'state': 'connected'});
    await emit(tester, {
      'type': 'confirm',
      'prompt': 'builder@mini에게 이 작업을 보낼까요?',
    });
    expect(find.text('builder@mini에게 이 작업을 보낼까요?'), findsOneWidget);
    await tester.tap(find.text('아니'));
    await tester.pump();
    expect(calls.where((call) => call.method == 'text').single.arguments, {
      'text': '아니',
    });
    expect(find.text('builder@mini에게 이 작업을 보낼까요?'), findsNothing);
  });

  testWidgets(
    'settings: theme, text size, sounds, voice and shortcuts are saved',
    (tester) async {
      await open(tester);
      await tester.tap(find.byKey(const Key('settings')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('palette-light')));
      await tester.pumpAndSettle();
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString('palette'), 'light');

      await tester.scrollUntilVisible(
        find.byKey(const Key('earcons')),
        150,
        scrollable: find.byType(Scrollable).last,
      );
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('earcons')));
      await tester.pumpAndSettle();
      expect(prefs.getBool('earcons'), isFalse);
      expect(
        calls
            .where((call) => call.method == 'callPrefs')
            .last
            .arguments['earcons'],
        isFalse,
      );

      await tester.scrollUntilVisible(
        find.byKey(const Key('voice')),
        150,
        scrollable: find.byType(Scrollable).last,
      );
      tester.widget<Slider>(find.byKey(const Key('voice'))).onChanged!(5);
      await tester.pumpAndSettle();
      expect(prefs.getInt('voice'), greaterThan(0));

      await tester.scrollUntilVisible(
        find.text('또렷한 말'),
        150,
        scrollable: find.byType(Scrollable).last,
      );
      expect(find.textContaining('옆 사람 말은 버려요'), findsOneWidget);
      await tester.tap(find.text('또렷한 말'));
      await tester.pumpAndSettle();
      expect(prefs.getString('filter'), 'normal');
      expect(
        calls
            .where((call) => call.method == 'callPrefs')
            .last
            .arguments['filter'],
        'normal',
      );

      await tester.scrollUntilVisible(
        find.byKey(const Key('pin-shortcut')),
        200,
        scrollable: find.byType(Scrollable).last,
      );
      await tester.tap(find.byKey(const Key('pin-shortcut')));
      await tester.pumpAndSettle();
      expect(methods(), contains('pinShortcut'));
    },
  );

  testWidgets('비서에게 알려 주기: profile, memories and MCP from the phone', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1080, 7000);
    tester.view.devicePixelRatio = 2.6;
    addTearDown(tester.view.reset);
    await open(tester);
    await tester.tap(find.byKey(const Key('settings')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('secretary')));
    await tester.pumpAndSettle();
    expect(find.text('음성 비서를 만드는 사람'), findsOneWidget);
    expect(find.text('아침엔 PR부터'), findsOneWidget);
    expect(find.text('말로 기억함'), findsOneWidget);

    await tester.enterText(find.byKey(const Key('usage')), '답은 두 문장으로');
    await tester.pump();
    await tester.tap(find.byKey(const Key('save-profile')));
    await tester.pumpAndSettle();
    Map request(int back) =>
        jsonDecode(
              calls
                      .where((c) => c.method == 'secretary')
                      .toList()
                      .reversed
                      .elementAt(back)
                      .arguments['request']
                  as String,
            )
            as Map;
    expect(request(0)['op'], 'profile.put');
    expect(request(0)['usage'], '답은 두 문장으로');

    await tester.tap(find.text('똑똑하게'));
    await tester.pumpAndSettle();
    expect(request(0), {'op': 'profile.put', 'brain': 'smart'});
    expect(find.textContaining('첫마디 2~3초'), findsOneWidget);

    await tester.scrollUntilVisible(
      find.byKey(const Key('mcp-kb-baryon')),
      200,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.tap(find.byKey(const Key('mcp-kb-baryon')));
    await tester.pumpAndSettle();
    expect(request(0)['mcp'], isEmpty);

    await tester.enterText(find.byKey(const Key('memory-input')), '회의록은 노션에');
    await tester.tap(find.byKey(const Key('memory-add')));
    await tester.pumpAndSettle();
    expect(request(0), {'op': 'memory.add', 'text': '회의록은 노션에'});
    expect(find.text('회의록은 노션에'), findsOneWidget);
  });

  testWidgets('알림: connected, what to allow, and a real test notification', (
    tester,
  ) async {
    await open(tester);
    await tester.tap(find.byKey(const Key('settings')));
    await tester.pumpAndSettle();
    expect(find.text('연결됨 · 화면이 꺼져 있어도 울려요'), findsOneWidget);
    expect(find.byKey(const Key('fix-fullScreen')), findsOneWidget);
    expect(
      find.byKey(const Key('fix-battery')),
      findsNothing,
    ); // already allowed

    await tester.tap(find.byKey(const Key('test-alarm')));
    await tester.pumpAndSettle();
    final sent =
        jsonDecode(
              calls
                      .lastWhere((call) => call.method == 'secretary')
                      .arguments['request']
                  as String,
            )
            as Map;
    expect(sent['op'], 'notify.add');
    expect(sent['alarm'], isTrue);
    expect(find.textContaining('15초 뒤에 울려요'), findsOneWidget);

    await tester.tap(find.byKey(const Key('alerts')));
    await tester.pumpAndSettle();
    expect(calls.lastWhere((call) => call.method == 'alerts').arguments, {
      'on': false,
    });
    expect(find.byKey(const Key('test-alarm')), findsNothing);
  });

  testWidgets('cards: what is shown, not said, under the spoken sentence', (
    tester,
  ) async {
    await open(tester);
    await emit(tester, {'type': 'heard', 'text': '이번 주 비용 보여 줘'});
    await emit(tester, {
      'type': 'card',
      'card': {
        'kind': 'bar',
        'title': '팀별 비용',
        'unit': '만원',
        'items': [
          {'label': '개발', 'value': 1200},
          {'label': '영업', 'value': 800.5},
        ],
      },
    });
    await emit(tester, {'type': 'said', 'text': '개발팀이 가장 많아요.'});
    await emit(tester, {
      'type': 'card',
      'card': {
        'kind': 'table',
        'title': '일정',
        'columns': ['시간', '제목'],
        'rows': [
          ['15:00', '미팅'],
        ],
      },
    });
    await emit(tester, {'type': 'agent_done'});
    await emit(tester, {'type': 'idle'});
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('card-bar')), findsOneWidget);
    expect(find.text('1,200만원'), findsOneWidget);
    expect(find.text('800.5만원'), findsOneWidget);
    expect(find.byKey(const Key('card-table')), findsOneWidget);
    expect(
      find.text('개발팀이 가장 많아요.'),
      findsOneWidget,
    ); // the words, in the same answer
    await emit(tester, {
      'type': 'card',
      'card': {
        'kind': 'line',
        'title': '방문자',
        'items': [
          {'label': '월', 'value': 10},
          {'label': '화', 'value': 14},
          {'label': '수', 'value': 9},
        ],
      },
    });
    await emit(tester, {
      'type': 'card',
      'card': {
        'kind': 'stats',
        'title': '오늘',
        'items': [
          {'label': '메일', 'value': 3, 'detail': '읽지 않음'},
          {'label': '회의', 'detail': '2건'},
        ],
      },
    });
    await emit(tester, {
      'type': 'card',
      'card': {'kind': 'text', 'title': '요약', 'body': '첫 줄\n- 할 일 하나'},
    });
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('card-line')), findsOneWidget);
    expect(find.text('2건'), findsOneWidget);
    expect(find.text('할 일 하나'), findsOneWidget);
    expect(SecretaryCard.number(1234567), '1,234,567');
  });

  testWidgets('a recording becomes text and goes to the secretary', (
    tester,
  ) async {
    await open(tester);
    await tester.tap(find.byKey(const Key('attach')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('attach-record')));
    await tester.pumpAndSettle();
    expect(methods(), contains('recordStart'));
    await emit(tester, {'type': 'recording', 'seconds': 65.0, 'level': 0.05});
    expect(find.text('녹음 중 1:05'), findsOneWidget);
    await tester.tap(find.byKey(const Key('record-stop')));
    await emit(tester, {
      'type': 'recorded',
      'path': '/cache/rec.pcm',
      'seconds': 192.0,
    });
    expect(calls.lastWhere((call) => call.method == 'upload').arguments, {
      'path': '/cache/rec.pcm',
    });
    await emit(tester, {
      'type': 'upload',
      'stage': 'transcribing',
      'done': 3,
      'total': 8,
    });
    expect(find.text('글로 옮기는 중'), findsOneWidget);
    await emit(tester, {
      'type': 'transcript',
      'text': '[00:00] 다음 주 화요일에 출시합니다',
      'seconds': 192.0,
      'path': '/cache/rec.pcm',
    });
    await tester.pump();
    final sent =
        jsonDecode(
              calls
                      .lastWhere((call) => call.method == 'attach')
                      .arguments['message']
                  as String,
            )
            as Map;
    expect(sent['text'], startsWith('[녹음 3분 12초 · 받아쓴 글]'));
    expect(sent['text'], contains('다음 주 화요일에 출시합니다'));
    expect(sent['shown'], {'kind': 'recording', 'seconds': 192.0});
    expect(find.text('녹음 3분 12초'), findsOneWidget);
    expect(find.byKey(const Key('record-bar')), findsNothing);
    // The gateway's "heard" for it does not add a second bubble.
    await emit(tester, {
      'type': 'heard',
      'text': sent['text'],
      'shown': sent['shown'],
    });
    expect(find.text('녹음 3분 12초'), findsOneWidget);
    await emit(tester, {'type': 'agent_done'});
    await tester.tap(find.byKey(const Key('transcript')));
    await tester.pumpAndSettle();
    expect(find.text('[00:00] 다음 주 화요일에 출시합니다'), findsOneWidget);
  });

  testWidgets('the app opens on numbered tasks, like a task list', (
    tester,
  ) async {
    await tester.pumpWidget(const BaryonVoiceApp());
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('board')), findsOneWidget);
    expect(find.text('#26 답을 기다려요'), findsOneWidget); // the headline
    expect(find.byKey(const Key('goal-26')), findsOneWidget);
    expect(find.byKey(const Key('goal-15')), findsOneWidget);
    expect(find.text('명령: 테스트 실행'), findsOneWidget); // what #15 is doing now
    expect(find.text('3개 더 보기'), findsOneWidget); // 8 closed, 5 shown
    await tester.tap(find.byKey(const Key('section-closed'))); // collapse
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('goal-30')), findsNothing);

    // A goal's page: its question, answered from the phone.
    await tester.tap(find.byKey(const Key('goal-26')));
    await tester.pumpAndSettle();
    expect(find.text('참여자는 몇 명으로 할까요?'), findsOneWidget);
    expect(find.text('설문 초안 작성'), findsWidgets);
    await tester.enterText(find.byKey(const Key('goal-answer')), '12명');
    await tester.tap(find.byKey(const Key('send-answer')));
    await tester.pumpAndSettle();
    final requests = [
      for (final call in calls.where((call) => call.method == 'secretary'))
        jsonDecode(call.arguments['request'] as String),
    ];
    expect(
      requests,
      anyElement(
        equals({'op': 'goal.answer', 'target': 'a1b2c3', 'text': '12명'}),
      ),
    );
    expect(find.textContaining('전달했어요'), findsOneWidget);

    // Ask the secretary about it: the conversation tab opens and the request goes out.
    await tester.ensureVisible(find.byKey(const Key('ask-about-goal')));
    await tester.tap(find.byKey(const Key('ask-about-goal')));
    for (var i = 0; i < 10; i++) {
      await tester.pump(
        const Duration(milliseconds: 100),
      ); // the reply spinner never settles
    }
    expect(calls.lastWhere((call) => call.method == 'text').arguments, {
      'text': '26번 작업 어떻게 됐어?',
    });
    expect(find.text('26번 작업 어떻게 됐어?'), findsOneWidget);
  });
}
