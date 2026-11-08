import 'dart:async';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/testing.dart';
import 'package:companion_mobile/api.dart';
import 'package:companion_mobile/native_push.dart';
import 'native_push_test.dart' as fixtures;

class DelayedRidDriver extends fixtures.FakeDriver {
  Completer<String>? ridGate;
  @override
  Future<String> registrationId() => ridGate?.future ?? super.registrationId();
}

void main() {
  test(
    'old background registration cannot suppress the new account registration',
    () async {
      final driver = DelayedRidDriver();
      final store = fixtures.MemoryStore();
      var oldPosts = 0, newPosts = 0;
      final oldApi = CompanionApi(
        'https://example.test',
        client: MockClient((r) async {
          if (r.method == 'POST') oldPosts++;
          return fixtures.response(r.method == 'GET' ? fixtures.config() : {});
        }),
      )..token = 'synthetic-old-user';
      final newApi = CompanionApi(
        'https://example.test',
        client: MockClient((r) async {
          if (r.method == 'POST') newPosts++;
          return fixtures.response(r.method == 'GET' ? fixtures.config() : {});
        }),
      )..token = 'synthetic-new-user';
      final push = NativeCarePushService(store, driver: driver);
      await push.enable(oldApi, () {});
      expect(oldPosts, 1);
      final oldRid = Completer<String>();
      driver.ridGate = oldRid;
      driver.eventsController.add(const NativePushEvent('connected', {}));
      await Future<void>.delayed(Duration.zero);
      await push.disconnect();
      driver.ridGate = null;
      try {
        await push.enable(newApi, () {});
        expect(
          newPosts,
          1,
          reason:
              'A registered success must bind this account, even while the previous account callback waits.',
        );
      } finally {
        oldRid.complete('old-registration');
        await Future<void>.delayed(Duration.zero);
        await push.disconnect();
        await driver.eventsController.close();
        oldApi.close();
        newApi.close();
      }
    },
  );
}
