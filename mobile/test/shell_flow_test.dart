import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:companion_mobile/api.dart';
import 'package:companion_mobile/app_state.dart';
import 'package:companion_mobile/screens.dart';
import 'package:companion_mobile/services.dart';

void main() {
  Map<String, dynamic> preferences() => {
    'enabled': true,
    'model_consent': false,
    'quiet_start': '22:00',
    'quiet_end': '08:00',
    'daily_limit': 2,
    'timezone': 'Asia/Shanghai',
    'reminder_categories': ['sleep', 'activity', 'meal'],
  };

  testWidgets(
    'all five screens render on a narrow phone and real manual record request is sent',
    (tester) async {
      tester.view.physicalSize = const Size(390, 844);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      Map<String, dynamic>? saved;
      final user = {
        'id': 'demo',
        'name': '小林',
        'is_demo': true,
        'timezone': 'Asia/Shanghai',
      };
      final api = CompanionApi(
        'https://companion.example',
        client: MockClient((request) async {
          final path = request.url.path;
          Map<String, dynamic> response = {'items': []};
          if (path == '/api/dashboard') {
            response = {
              'user': user,
              'metrics': {
                'steps': 2100,
                'sleep_hours': 7.5,
                'workout_minutes': 20,
                'water_ml': 250,
              },
              'mode': 'local',
              'series': [],
              'insights': [],
            };
          }
          if (path == '/api/preferences') response = preferences();
          if (path == '/api/integrations/status') {
            response = {
              'open_wearables': {'configured': false},
              'model': {'configured': false},
              'push': {'configured': false},
            };
          }
          if (path == '/api/events' && request.method == 'POST') {
            saved = jsonDecode(request.body) as Map<String, dynamic>;
            response = {'event': saved, 'created': true};
          }
          return http.Response.bytes(utf8.encode(jsonEncode(response)), 200);
        }),
      );
      final state = CompanionState(api, user);
      await tester.pumpWidget(
        MaterialApp(
          home: CompanionShell(
            state: state,
            health: HealthSyncService(const SessionStore()),
            push: PushService(const SessionStore()),
            onLogout: ({bool deleteAccount = false}) async {},
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.textContaining('合成数据'), findsOneWidget);
      await tester.ensureVisible(find.text('饮水'));
      await tester.tap(find.widgetWithText(ActionChip, '饮水'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('保存'));
      await tester.pumpAndSettle();
      expect(saved?['kind'], 'water');
      expect(saved?['value'], 250);
      expect(DateTime.parse(saved!['start_at'] as String).isUtc, isTrue);
      for (final label in ['时间线', '记忆', '聊聊', '我的']) {
        await tester.tap(find.text(label));
        await tester.pumpAndSettle();
        expect(
          tester.takeException(),
          isNull,
          reason: '$label should fit a 390px phone',
        );
      }
      await tester.pumpWidget(const SizedBox.shrink());
      state.dispose();
    },
  );

  testWidgets(
    'model consent defaults off and is explicitly sent in preference changes',
    (tester) async {
      Map<String, dynamic>? saved;
      final state = CompanionState(
        CompanionApi(
          'https://companion.example',
          client: MockClient((request) async {
            if (request.method == 'PATCH') {
              saved = jsonDecode(request.body) as Map<String, dynamic>;
            }
            final response = request.url.path == '/api/preferences'
                ? preferences()
                : <String, dynamic>{'items': []};
            return http.Response(jsonEncode(response), 200);
          }),
        ),
        {},
      )..preferences = preferences();
      Future<bool> perform(
        Future<void> Function() action, {
        String? message,
      }) async {
        await action();
        return true;
      }

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: PreferencesForm(state: state, perform: perform),
            ),
          ),
        ),
      );
      final consent = find.widgetWithText(SwitchListTile, '允许外部模型处理必要摘要');
      expect(consent, findsOneWidget);
      expect(tester.widget<SwitchListTile>(consent).value, isFalse);
      await tester.tap(consent);
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.text('保存提醒偏好'));
      await tester.tap(find.text('保存提醒偏好'));
      await tester.pumpAndSettle();
      expect(saved?['model_consent'], isTrue);
    },
  );
}
