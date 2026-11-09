import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:companion_mobile/api.dart';
import 'package:companion_mobile/app_state.dart';
import 'package:companion_mobile/care_state.dart';
import 'package:companion_mobile/care_screens.dart';

http.Response response(Map<String, dynamic> value) =>
    http.Response.bytes(utf8.encode(jsonEncode(value)), 200);
void main() {
  testWidgets(
    'cloud admin interval explains scheduler and rejects unsupported frequency before PATCH',
    (tester) async {
      var patches = 0;
      final state = CompanionState(
        CompanionApi(
          'https://example.test',
          client: MockClient((r) async {
            if (r.method == 'PATCH') patches++;
            if (r.url.path.endsWith('/care-config')) {
              return response({
                'enabled': false,
                'analysis_interval_minutes': 120,
                'scheduler_interval_minutes': 60,
                'min_analysis_interval_minutes': 60,
              });
            }
            if (r.url.path.endsWith('/push-config')) {
              return response({
                'provider': 'disabled',
                'client_options': {'firebase': {}, 'manufacturers': {}},
              });
            }
            return response({'mode': 'disabled'});
          }),
        ),
        {'id': 'admin', 'is_admin': true},
      );
      await tester.pumpWidget(MaterialApp(home: CareAdminPage(state: state)));
      await tester.pumpAndSettle();
      expect(find.textContaining('每 60 分钟'), findsOneWidget);
      await tester.enterText(
        find.byKey(const Key('admin-analysis_interval_minutes')),
        '15',
      );
      await tester.ensureVisible(find.text('保存关心策略'));
      await tester.tap(find.text('保存关心策略'));
      await tester.pumpAndSettle();
      expect(patches, 0);
      expect(find.textContaining('60'), findsWidgets);
      await tester.pumpWidget(const SizedBox.shrink());
      state.dispose();
    },
  );
  testWidgets(
    'care inbox opens context, marks read and lets user reply without clearing a pending conversation',
    (tester) async {
      tester.view.physicalSize = const Size(390, 844);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      Map<String, dynamic>? sent;
      final api = CompanionApi(
        'https://example.test',
        client: MockClient((r) async {
          if (r.url.path == '/api/care') {
            return response({
              'items': [
                {
                  'id': 'c',
                  'title': '明天面试，想听听你的感受',
                  'preview': '这次想怎么准备？',
                  'unread': true,
                  'message_count': 1,
                  'created_at': '2026-11-08T10:00:00Z',
                },
              ],
              'unread_count': 1,
              'status': {
                'enabled': true,
                'model_allowed': true,
                'available': true,
              },
            });
          }
          if (r.url.path.endsWith('/read')) return response({'read': true});
          if (r.url.path.endsWith('/reply')) {
            sent = jsonDecode(r.body);
            return response({
              'thread': {'id': 'c', 'title': '明天面试，想听听你的感受'},
              'messages': [
                {'id': 'a', 'role': 'assistant', 'content': '这次想怎么准备？'},
                {
                  'id': 'u',
                  'role': 'user',
                  'content': sent!['message'],
                  'request_id': sent!['request_id'],
                  'reply_status': 'pending',
                },
              ],
              'reply_status': 'pending',
            });
          }
          return response({
            'thread': {'id': 'c', 'title': '明天面试，想听听你的感受', 'source_thread': {'id': 't', 'title': '原来的面试标题', 'revision': 1}},
            'messages': [
              {'id': 'a', 'role': 'assistant', 'content': '这次想怎么准备？'},
            ],
          });
        }),
      );
      final care = CareState(api);
      await care.refresh();
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(body: CareInboxPage(care: care)),
        ),
      );
      await tester.tap(find.text('明天面试，想听听你的感受'));
      await tester.pumpAndSettle();
      expect(find.text('这次想怎么准备？'), findsOneWidget);
      expect(find.textContaining('原来的面试标题'), findsOneWidget);
      await tester.enterText(
        find.byKey(const Key('care-reply-input')),
        '有些紧张，想一起理清',
      );
      await tester.tap(find.byKey(const Key('care-reply-send')));
      await tester.pumpAndSettle();
      expect(sent?['message'], '有些紧张，想一起理清');
      expect(find.textContaining('已保存'), findsOneWidget);
      expect(find.text('重试小伴回应'), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox.shrink());
      care.dispose();
      api.close();
    },
  );

  testWidgets('ordinary user cannot fetch administrator settings', (
    tester,
  ) async {
    var calls = 0;
    final state = CompanionState(
      CompanionApi(
        'https://example.test',
        client: MockClient((_) async {
          calls++;
          return response({});
        }),
      ),
      {'id': 'normal', 'is_admin': false},
    );
    await tester.pumpWidget(MaterialApp(home: CareAdminPage(state: state)));
    await tester.pumpAndSettle();
    expect(find.textContaining('仅管理员'), findsOneWidget);
    expect(calls, 0);
    await tester.pumpWidget(const SizedBox.shrink());
    state.dispose();
  });
}
