import 'dart:convert';

import 'package:baryon_voice/main.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  final calls = <MethodCall>[];

  setUp(() {
    calls.clear();
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(
      const MethodChannel('baryon/voice'),
      (call) async {
        calls.add(call);
        return call.method == 'config' ? {'server': 'ws://127.0.0.1:9093/v1/voice/call', 'hasToken': true, 'inCall': false} : true;
      },
    );
  });

  Future<void> emit(WidgetTester tester, Map<String, dynamic> event) async {
    await TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.handlePlatformMessage(
      'baryon/voice/events',
      const StandardMethodCodec().encodeSuccessEnvelope(jsonEncode(event)),
      (_) {},
    );
    await tester.pump();
  }

  Iterable<String> methods() => calls.map((call) => call.method);

  testWidgets('voice call: conversation, speaker, progress steps and background tasks', (tester) async {
    await tester.pumpWidget(const BaryonVoiceApp());
    await tester.pump();
    await tester.tap(find.byIcon(Icons.call));
    await tester.pump();
    expect(methods(), contains('start'));

    await emit(tester, {'type': 'state', 'state': 'connected'});
    await emit(tester, {'type': 'heard', 'text': '로그인 버그 빌더한테 맡겨'});
    await emit(tester, {'type': 'activity', 'id': 'a1', 'label': '플릿 상태를 보고 있어요.'});
    await emit(tester, {'type': 'agent_text', 'delta': '빌더에게 맡길게요.'});
    expect(find.text('플릿 상태를 보고 있어요.'), findsWidgets);
    expect(find.text('빌더에게 맡길게요.'), findsOneWidget); // live text while it works
    expect(find.byType(CircularProgressIndicator), findsOneWidget);

    await emit(tester, {'type': 'activity_done', 'id': 'a1', 'error': false});
    expect(find.byIcon(Icons.check_circle), findsOneWidget);
    await emit(tester, {'type': 'task', 'to': 'builder@mini', 'summary': '로그인 버그 수정', 'status': 'sent'});
    expect(find.text('로그인 버그 수정'), findsOneWidget);
    expect(find.textContaining('백그라운드 작업'), findsOneWidget);

    await emit(tester, {'type': 'said', 'text': '빌더에게 맡겼어요.'});
    await emit(tester, {'type': 'agent_done'});
    expect(find.byType(CircularProgressIndicator), findsNothing);
    expect(find.text('빌더에게 맡겼어요.'), findsOneWidget);

    await tester.tap(find.byIcon(Icons.volume_up));
    await tester.pump();
    expect(calls.where((call) => call.method == 'speaker').single.arguments, {'on': true});
  });

  testWidgets('typing works without a call, and dictation fills the box', (tester) async {
    await tester.pumpWidget(const BaryonVoiceApp());
    await tester.pump();
    await tester.enterText(find.byKey(const Key('input')), '오늘 일정 알려줘');
    await tester.tap(find.byKey(const Key('send')));
    await tester.pump();
    expect(calls.where((call) => call.method == 'text').single.arguments, {'text': '오늘 일정 알려줘'});
    expect(find.text('오늘 일정 알려줘'), findsOneWidget);

    await tester.tap(find.byKey(const Key('dictate')));
    await tester.pump();
    expect(calls.where((call) => call.method == 'dictate').single.arguments, {'on': true});
    await emit(tester, {'type': 'dictation', 'text': '메일도 정리해줘'});
    expect(find.widgetWithText(TextField, '메일도 정리해줘'), findsOneWidget);
  });

  testWidgets('a confirmation can be answered by tapping', (tester) async {
    await tester.pumpWidget(const BaryonVoiceApp());
    await tester.pump();
    await emit(tester, {'type': 'state', 'state': 'connected'});
    await emit(tester, {'type': 'confirm', 'prompt': 'builder@mini에게 이 작업을 보낼까요?'});
    expect(find.text('builder@mini에게 이 작업을 보낼까요?'), findsOneWidget);

    await tester.tap(find.text('아니'));
    await tester.pump();
    expect(calls.where((call) => call.method == 'text').single.arguments, {'text': '아니'});
    expect(find.text('builder@mini에게 이 작업을 보낼까요?'), findsNothing);
  });
}

