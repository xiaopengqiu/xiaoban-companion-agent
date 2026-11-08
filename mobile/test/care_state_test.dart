import 'dart:async';
import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:companion_mobile/api.dart';
import 'package:companion_mobile/care_state.dart';

http.Response jsonResponse(Map<String, dynamic> value, [int status = 200]) =>
    http.Response.bytes(utf8.encode(jsonEncode(value)), status);

void main() {
  test(
    'server preference changes on another client override cached local opt-in',
    () async {
      var enabled = false;
      final api = CompanionApi(
        'https://example.test',
        client: MockClient((r) async {
          if (r.url.path == '/api/care/preferences') {
            enabled = (jsonDecode(r.body) as Map)['enabled'] == true;
            return jsonResponse({'enabled': enabled});
          }
          return jsonResponse({
            'items': [],
            'unread_count': 0,
            'status': {'enabled': enabled},
          });
        }),
      );
      final care = CareState(api);
      await care.setEnabled(true, consentConfirmed: true);
      expect(care.enabled, true);
      enabled = false;
      await care.refresh();
      expect(care.enabled, false);
      care.dispose();
      api.close();
    },
  );
  test(
    'no-care result stays empty and never fetches administrator resources',
    () async {
      final calls = <String>[];
      final care = CareState(
        CompanionApi(
          'https://example.test',
          client: MockClient((r) async {
            calls.add(r.url.path);
            return jsonResponse({
              'items': [],
              'unread_count': 0,
              'status': {'enabled': true, 'reason_code': 'no_contact'},
            });
          }),
        ),
      );
      await care.refresh();
      expect(care.threads, isEmpty);
      expect(care.unreadCount, 0);
      expect(calls, ['/api/care']);
      care.dispose();
    },
  );

  test(
    'older service care failure does not break base application state',
    () async {
      final care = CareState(
        CompanionApi(
          'https://example.test',
          client: MockClient(
            (_) async => jsonResponse({'detail': 'missing'}, 404),
          ),
        ),
      );
      await care.refresh();
      expect(care.available, isFalse);
      expect(care.error, isNotNull);
      expect(care.threads, isEmpty);
      care.dispose();
    },
  );

  test(
    'care opt-in requires explicit summary consent and disabling preserves chat consent',
    () async {
      final sent = <Map<String, dynamic>>[];
      final api = CompanionApi(
        'https://example.test',
        client: MockClient((r) async {
          sent.add({
            'path': r.url.path,
            'method': r.method,
            if (r.body.isNotEmpty) 'body': jsonDecode(r.body),
          });
          return jsonResponse(
            r.url.path == '/api/care'
                ? {'items': [], 'unread_count': 0, 'status': {}}
                : {'enabled': true},
          );
        }),
      );
      final care = CareState(api);
      await expectLater(
        care.setEnabled(true, consentConfirmed: false),
        throwsA(isA<ApiException>()),
      );
      expect(sent, isEmpty);
      await care.setEnabled(true, consentConfirmed: true);
      expect(sent[0], {
        'path': '/api/preferences',
        'method': 'PATCH',
        'body': {'model_consent': true},
      });
      expect(sent[1], {
        'path': '/api/care/preferences',
        'method': 'PATCH',
        'body': {'enabled': true},
      });
      sent.clear();
      await care.setEnabled(false);
      expect(sent.first['body'], {'enabled': false});
      expect(sent.where((x) => x['path'] == '/api/preferences'), isEmpty);
      care.dispose();
    },
  );

  test(
    'pending reply retries original request once and preserves a newer draft',
    () async {
      final replies = <Map<String, dynamic>>[];
      final care = CareState(
        CompanionApi(
          'https://example.test',
          client: MockClient((r) async {
            if (r.url.path.endsWith('/reply')) {
              final body = jsonDecode(r.body) as Map<String, dynamic>;
              replies.add(body);
              return jsonResponse({
                'thread': {'id': 'care1'},
                'messages': [
                  {
                    'id': 'u1',
                    'role': 'user',
                    'content': body['message'],
                    'request_id': body['request_id'],
                    'reply_status': replies.length == 1
                        ? 'pending'
                        : 'complete',
                  },
                  if (replies.length > 1)
                    {
                      'id': 'a1',
                      'role': 'assistant',
                      'content': '当然，我记得你正在准备面试。',
                      'reply_to_request_id': body['request_id'],
                    },
                ],
                'reply_status': replies.length == 1 ? 'pending' : 'complete',
              });
            }
            return jsonResponse(
              r.url.path == '/api/care'
                  ? {'items': [], 'unread_count': 0, 'status': {}}
                  : {'read': true},
            );
          }),
        ),
      );
      care.setDraft('care1', '今天有点紧张');
      await care.reply('care1', '今天有点紧张');
      expect(care.pendingReply('care1')?['content'], '今天有点紧张');
      care.setDraft('care1', '我还想补充一点');
      await care.retryReply('care1');
      expect(replies.length, 2);
      expect(replies[0], replies[1]);
      expect(care.draft('care1'), '我还想补充一点');
      expect(care.pendingReply('care1'), isNull);
      expect(
        care.messages('care1').where((m) => m['role'] == 'assistant').length,
        1,
      );
      care.dispose();
    },
  );

  test('disposed account cannot commit a delayed care response', () async {
    final done = Completer<http.Response>();
    final care = CareState(
      CompanionApi(
        'https://example.test',
        client: MockClient((_) => done.future),
      ),
    );
    final future = care.refresh();
    care.dispose();
    done.complete(
      jsonResponse({
        'items': [
          {'id': 'old-account'},
        ],
        'unread_count': 1,
        'status': {},
      }),
    );
    await future;
    expect(care.threads, isEmpty);
  });

  test(
    'lost reply response retries the same request ID rather than duplicating a saved turn',
    () async {
      final calls = <Map<String, dynamic>>[];
      final care = CareState(
        CompanionApi(
          'https://example.test',
          client: MockClient((r) async {
            if (r.url.path.endsWith('/reply')) {
              final body = jsonDecode(r.body) as Map<String, dynamic>;
              calls.add(body);
              if (calls.length == 1) {
                throw http.ClientException('response lost');
              }
              return jsonResponse({
                'thread': {'id': 'c'},
                'messages': [
                  {
                    'id': 'u',
                    'role': 'user',
                    'content': body['message'],
                    'request_id': body['request_id'],
                    'reply_status': 'complete',
                  },
                  {
                    'id': 'a',
                    'role': 'assistant',
                    'content': '我在这里。',
                    'reply_to_request_id': body['request_id'],
                  },
                ],
                'reply_status': 'complete',
              });
            }
            return jsonResponse(
              r.url.path == '/api/care'
                  ? {'items': [], 'unread_count': 0, 'status': {}}
                  : {'read': true},
            );
          }),
        ),
      );
      await expectLater(care.reply('c', '想接着聊'), throwsA(isA<ApiException>()));
      await care.reply('c', '想接着聊');
      expect(calls[0]['request_id'], calls[1]['request_id']);
      care.dispose();
    },
  );

  test(
    'ordinary account never invokes administrator endpoints and empty secrets are omitted',
    () async {
      final sent = <Map<String, dynamic>>[];
      var admin = false;
      final settings = CareAdminService(
        CompanionApi(
          'https://example.test',
          client: MockClient((r) async {
            sent.add({
              'path': r.url.path,
              if (r.body.isNotEmpty) 'body': jsonDecode(r.body),
            });
            return jsonResponse({});
          }),
        ),
        isAdmin: () => admin,
      );
      await expectLater(settings.load(), throwsA(isA<ApiException>()));
      expect(sent, isEmpty);
      admin = true;
      await settings.savePush({
        'provider': 'jpush',
        'enabled': true,
        'app_key': 'public',
        'master_secret': '   ',
      });
      expect(
        (sent.single['body'] as Map).containsKey('master_secret'),
        isFalse,
      );
      admin = false;
      await expectLater(
        settings.saveCare({'enabled': true}),
        throwsA(isA<ApiException>()),
      );
      expect(sent.length, 1);
    },
  );
}
