import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:companion_mobile/api.dart';
import 'package:companion_mobile/app_state.dart';

void main() {
  test(
    'external memory failure stays visible after local save and clears only after confirmed retry',
    () async {
      final state = CompanionState(
        CompanionApi(
          'https://companion.example',
          client: MockClient((request) async {
            final response =
                [
                  '/api/events',
                  '/api/memories',
                  '/api/chat',
                  '/api/reminders',
                ].contains(request.url.path)
                ? {'items': []}
                : <String, dynamic>{};
            return http.Response(jsonEncode(response), 200);
          }),
        ),
        {},
      );
      await state.mutate(() async => {'memory_sync': 'failed'});
      expect(state.syncWarning, contains('待重试'));
      await state.refresh();
      expect(state.syncWarning, isNotNull);
      await state.mutate(() async => {'memory_sync': 'cleared'});
      expect(state.syncWarning, isNull);
      state.dispose();
    },
  );
}
