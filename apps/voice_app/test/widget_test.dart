import 'dart:convert';

import 'package:baryon_voice/main.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  final calls = <MethodCall>[];

  setUp(() {
    calls.clear();
    SharedPreferences.setMockInitialValues({});
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(const MethodChannel('baryon/voice'), (
          call,
        ) async {
          calls.add(call);
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
    await tester.pumpWidget(const BaryonVoiceApp());
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
}
