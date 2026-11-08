import 'dart:async';
import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:companion_mobile/api.dart';
import 'package:companion_mobile/native_push.dart';
import 'package:companion_mobile/services.dart';

class MemoryStore extends SessionStore {
  final values = <String, String>{};
  @override
  Future<String?> read(String k) async => values[k];
  @override
  Future<void> write(String k, String v) async {
    values[k] = v;
  }

  @override
  Future<void> delete(String k) async {
    values.remove(k);
  }
}

class FakeDriver implements NativePushDriver {
  final eventsController = StreamController<NativePushEvent>.broadcast(
    sync: true,
  );
  bool permitted = true;
  int initializes = 0, stops = 0;
  bool? asked;
  String rid = 'own-registration';
  Map<String, dynamic>? config;
  Map<String, dynamic> launch = {};
  Completer<void>? initializeGate;
  Map<String, dynamic>? earlyNotification;
  @override
  Stream<NativePushEvent> get events => eventsController.stream;
  @override
  Future<bool> permission({required bool request}) async {
    asked = request;
    return permitted;
  }

  @override
  Future<Map<String, dynamic>> initialize(Map<String, dynamic> c) async {
    initializes++;
    config = c;
    if (earlyNotification != null) {
      eventsController.add(NativePushEvent('opened', earlyNotification!));
    }
    await initializeGate?.future;
    return {'rebuild_required': false};
  }

  @override
  Future<String> registrationId() async => rid;
  @override
  Future<Map<String, dynamic>> launchNotification() async => launch;
  @override
  Future<void> stop() async {
    stops++;
  }
}

class GatedStore extends MemoryStore {
  final entered = Completer<void>(), gate = Completer<void>();
  @override
  Future<void> write(String k, String v) async {
    if (k == 'native_push_opted_in') {
      entered.complete();
      await gate.future;
    }
    await super.write(k, v);
  }
}

Map<String, dynamic> config() => {
  'provider': 'jpush',
  'enabled': true,
  'configured': true,
  'app_key': '123456789012345678901234',
  'revision': 1,
  'client_options': {'firebase': {}, 'manufacturers': {}},
  'master_secret': 'must-not-reach-sdk',
};
http.Response response(Map<String, dynamic> value) =>
    http.Response.bytes(utf8.encode(jsonEncode(value)), 200);
void main() {
  test(
    'logout during opt-in persistence cannot initialize SDK or restore stale opt-in',
    () async {
      final store = GatedStore(), driver = FakeDriver();
      final api = CompanionApi(
        'https://example.test',
        client: MockClient(
          (r) async => response(r.method == 'GET' ? config() : {}),
        ),
      );
      final push = NativeCarePushService(store, driver: driver);
      final pending = expectLater(
        push.enable(api, () {}),
        throwsA(isA<ApiException>()),
      );
      await store.entered.future;
      await push.disconnect();
      store.gate.complete();
      await pending;
      expect(driver.initializes, 0);
      expect(store.values, isEmpty);
      await driver.eventsController.close();
      api.close();
    },
  );
  test(
    'cached Android cold start click arriving before registration is retained',
    () async {
      final driver = FakeDriver()
        ..earlyNotification = {
          'extras': {'care_id': 'cold_early'},
        };
      final opened = <String>[];
      final api = CompanionApi(
        'https://example.test',
        client: MockClient(
          (r) async => response(r.method == 'GET' ? config() : {}),
        ),
      );
      final push = NativeCarePushService(MemoryStore(), driver: driver);
      await push.enable(api, () {}, onOpenCare: opened.add);
      expect(opened, ['cold_early']);
      await push.disconnect();
      await driver.eventsController.close();
      api.close();
    },
  );
  test(
    'unconfigured service never initializes SDK or requests device permission',
    () async {
      final driver = FakeDriver(), store = MemoryStore();
      final push = NativeCarePushService(store, driver: driver);
      final api = CompanionApi(
        'https://example.test',
        client: MockClient(
          (_) async => response({'provider': 'disabled', 'configured': false}),
        ),
      );
      await expectLater(push.enable(api, () {}), throwsA(isA<ApiException>()));
      expect(driver.initializes, 0);
      expect(driver.asked, isNull);
      expect(store.values, isEmpty);
      await driver.eventsController.close();
      api.close();
    },
  );
  test('notification denial does not initialize or register push', () async {
    final driver = FakeDriver()..permitted = false;
    var posted = 0;
    final api = CompanionApi(
      'https://example.test',
      client: MockClient((r) async {
        if (r.method == 'POST') posted++;
        return response(config());
      }),
    );
    await expectLater(
      NativeCarePushService(MemoryStore(), driver: driver).enable(api, () {}),
      throwsA(isA<ApiException>()),
    );
    expect(driver.initializes, 0);
    expect(posted, 0);
    expect(driver.asked, true);
    await driver.eventsController.close();
    api.close();
  });
  test(
    'opted-in device uses only public bootstrap, registers scoped revision and unregisters on logout',
    () async {
      final driver = FakeDriver(),
          store = MemoryStore(),
          calls = <Map<String, dynamic>>[];
      final api = CompanionApi(
        'https://example.test',
        client: MockClient((r) async {
          calls.add({
            'path': r.url.path,
            'method': r.method,
            if (r.body.isNotEmpty) 'body': jsonDecode(r.body),
          });
          return response(r.method == 'GET' ? config() : {'registered': true});
        }),
      );
      final push = NativeCarePushService(store, driver: driver);
      await push.enable(api, () {});
      expect(driver.config!.containsKey('master_secret'), false);
      expect(calls[1]['body'], {
        'provider': 'jpush',
        'registration_id': 'own-registration',
        'config_revision': 1,
      });
      expect(store.values['native_push_registered'], 'true');
      await push.disconnect();
      expect(calls.last, {
        'path': '/api/push/devices',
        'method': 'DELETE',
        'body': {'registration_id': 'own-registration'},
      });
      expect(driver.stops, 1);
      expect(store.values['native_push_registered'], isNull);
      await driver.eventsController.close();
      api.close();
    },
  );
  test(
    'cold start tap is deduplicated and stale callbacks are ignored after logout',
    () async {
      final driver = FakeDriver()
        ..launch = {
          'extras': {'care_id': 'care_1', 'type': 'care'},
        };
      final opened = <String>[];
      var refreshed = 0;
      final api = CompanionApi(
        'https://example.test',
        client: MockClient(
          (r) async => response(r.method == 'GET' ? config() : {}),
        ),
      );
      final push = NativeCarePushService(MemoryStore(), driver: driver);
      await push.enable(api, () => refreshed++, onOpenCare: opened.add);
      driver.eventsController.add(
        NativePushEvent('opened', {'extras': '{"care_id":"care_1"}'}),
      );
      expect(opened, ['care_1']);
      driver.eventsController.add(
        NativePushEvent('received', {
          'extras': {'care_id': 'care_1'},
        }),
      );
      expect(refreshed, 1);
      await push.disconnect();
      driver.eventsController.add(
        NativePushEvent('opened', {'care_id': 'care_2'}),
      );
      expect(opened, ['care_1']);
      await driver.eventsController.close();
      api.close();
    },
  );
  test('restoring an unapproved account never touches native SDK', () async {
    final driver = FakeDriver();
    final push = NativeCarePushService(MemoryStore(), driver: driver);
    final api = CompanionApi(
      'https://example.test',
      client: MockClient(
        (_) async => throw StateError('network should not be used'),
      ),
    );
    await push.restore(api, () {});
    expect(driver.initializes, 0);
    await driver.eventsController.close();
    api.close();
  });
  test(
    'overlapping enable cannot report success without its own registration',
    () async {
      final driver = FakeDriver()..initializeGate = Completer<void>();
      final api = CompanionApi(
        'https://example.test',
        client: MockClient(
          (r) async => response(r.method == 'GET' ? config() : {}),
        ),
      );
      final push = NativeCarePushService(MemoryStore(), driver: driver);
      final first = push.enable(api, () {});
      await Future<void>.delayed(Duration.zero);
      await expectLater(push.enable(api, () {}), throwsA(isA<ApiException>()));
      driver.initializeGate!.complete();
      await first;
      expect(driver.initializes, 1);
      await push.disconnect();
      await driver.eventsController.close();
      api.close();
    },
  );
  test(
    'logout during registration removes a token bound by late response',
    () async {
      final driver = FakeDriver(), store = MemoryStore();
      final gate = Completer<http.Response>();
      final deletes = <String>[];
      final posted = Completer<void>();
      final api = CompanionApi(
        'https://example.test',
        client: MockClient((r) async {
          if (r.method == 'GET') return response(config());
          if (r.method == 'POST') {
            posted.complete();
            return gate.future;
          }
          deletes.add((jsonDecode(r.body) as Map)['registration_id'] as String);
          return response({});
        }),
      );
      final push = NativeCarePushService(store, driver: driver);
      final pending = push.enable(api, () {});
      final error = expectLater(pending, throwsA(isA<ApiException>()));
      await posted.future;
      await push.disconnect();
      gate.complete(response({}));
      await error;
      expect(deletes, ['own-registration']);
      expect(store.values, isEmpty);
      await driver.eventsController.close();
      api.close();
    },
  );
  test(
    'notification target rejects arbitrary URLs and supports known nested extras only',
    () {
      expect(
        careIdFromNotification({
          'extras': {'care_id': 'https://attacker.test'},
        }),
        isNull,
      );
      expect(
        careIdFromNotification({'extras': '{"care_id":"abc-123"}'}),
        'abc-123',
      );
      expect(
        careIdFromNotification({
          'random': {'care_id': 'not-a-known-container'},
        }),
        isNull,
      );
    },
  );
}
