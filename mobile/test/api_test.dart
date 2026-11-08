import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:companion_mobile/api.dart';

void main() {
  test(
    'decodes Chinese JSON as UTF8 and sends scoped Bearer credentials',
    () async {
      final api = CompanionApi(
        'http://127.0.0.1:8765/api/',
        client: MockClient((request) async {
          expect(request.url.path, '/api/memories');
          expect(request.headers['Authorization'], 'Bearer user-session');
          return http.Response.bytes(
            utf8.encode('{"items":[{"text":"喜欢饭后散步"}]}'),
            200,
          );
        }),
      );
      api.token = 'user-session';
      expect((await api.get('/memories'))['items'][0]['text'], '喜欢饭后散步');
    },
  );

  test(
    'preserves structured validation errors without leaking request data',
    () async {
      final api = CompanionApi(
        'https://companion.example',
        client: MockClient(
          (_) async => http.Response(
            jsonEncode({
              'detail': [
                {'msg': '密码至少 10 位'},
              ],
            }),
            422,
            headers: {'content-type': 'application/json; charset=utf-8'},
          ),
        ),
      );
      await expectLater(
        api.post('/auth/register', {'password': 'secret'}),
        throwsA(
          isA<ApiException>().having(
            (e) => e.message,
            'message',
            contains('密码至少 10 位'),
          ),
        ),
      );
    },
  );

  test('a non-JSON success is an actionable format error', () async {
    final api = CompanionApi(
      'https://companion.example',
      client: MockClient(
        (_) async => http.Response('<html>proxy error</html>', 200),
      ),
    );
    await expectLater(api.get('/dashboard'), throwsA(isA<ApiException>()));
  });

  test('rejects unsafe server configuration and release cleartext', () {
    expect(
      () => normalizeServerUrl('file:///secret'),
      throwsA(isA<ApiException>()),
    );
    expect(
      () => normalizeServerUrl('http://example.com', requireHttps: true),
      throwsA(isA<ApiException>()),
    );
    expect(
      normalizeServerUrl('https://example.com/api/'),
      'https://example.com',
    );
    expect(
      () => normalizeServerUrl('https://user:secret@example.com'),
      throwsA(isA<ApiException>()),
    );
  });

  test('rejects item lists with malformed records', () {
    expect(
      () => decodeItems({
        'items': [2],
      }),
      throwsA(isA<ApiException>()),
    );
    expect(
      decodeItems({
        'items': [
          {'id': 'a'},
        ],
      }).single['id'],
      'a',
    );
  });

  test('invalid and absent health SDK tokens never count as configured', () {
    expect(
      () => WearableConnection.fromJson({'configured': false, 'detail': '未配置'}),
      throwsA(isA<ApiException>()),
    );
    expect(
      () => WearableConnection.fromJson({
        'configured': true,
        'host': 'https://health.example',
      }),
      throwsA(isA<ApiException>()),
    );
    final connection = WearableConnection.fromJson({
      'configured': true,
      'host': 'https://health.example',
      'wearable_user_id': 'bound-user',
      'access_token': 'a',
      'refresh_token': 'r',
    });
    expect(connection.userId, 'bound-user');
  });
}
