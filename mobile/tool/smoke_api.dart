import 'dart:io';
import 'package:companion_mobile/api.dart';

// Real HTTP smoke test. Creates and deletes only its own isolated demo user.
Future<void> main(List<String> args) async {
  final api = CompanionApi(args.isEmpty ? 'http://127.0.0.1:8765' : args.first);
  var authenticated = false;
  void require(bool condition, String message) {
    if (!condition) throw StateError(message);
  }

  try {
    final auth = await api.post('/demo', {});
    api.token = auth['token'] as String;
    authenticated = true;
    require(
      (auth['user'] as Map)['is_demo'] == true,
      'Must be an isolated demo',
    );
    final dashboard = await api.get('/dashboard');
    require(dashboard['metrics'] is Map, 'Dashboard metrics');
    final created = await api.post('/events', {
      'kind': 'water',
      'value': 250,
      'unit': 'ml',
      'start_at': DateTime.now().toUtc().toIso8601String(),
      'source': 'manual',
      'metadata': {'note': 'Flutter HTTP smoke'},
    });
    final id = (created['event'] as Map)['id'];
    require(
      decodeItems(await api.get('/events')).any((event) => event['id'] == id),
      'Persisted water record',
    );
    final memoryResponse = await api.post('/memories', {
      'text': '喜欢饭后散步',
      'category': 'preference',
    });
    final memory = memoryResponse['memory'] is Map
        ? memoryResponse['memory'] as Map
        : memoryResponse;
    require(memory['id'] != null, 'Memory identity');
    await api.patch('/memories/${memory['id']}', {'text': '喜欢午饭后散步'});
    require(
      decodeItems(
        await api.get('/memories'),
      ).any((m) => m['text'] == '喜欢午饭后散步'),
      'Memory correction',
    );
    final chat = await api.post('/chat', {'message': '你记住了我什么？'});
    require(chat['reply'] is String, 'Chat reply');
    await api.patch('/preferences', {
      'enabled': true,
      'model_consent': false,
      'quiet_start': '22:00',
      'quiet_end': '08:00',
      'daily_limit': 2,
      'reminder_categories': ['sleep', 'activity', 'meal'],
      'timezone': 'Asia/Shanghai',
    });
    require(
      (await api.get('/preferences'))['model_consent'] == false,
      'Consent stays explicit',
    );
    await api.post('/evaluate', {});
    final reminders = decodeItems(await api.get('/reminders'));
    if (reminders.isNotEmpty) {
      await api.post('/reminders/${reminders.first['id']}/feedback', {
        'action': 'dismiss',
      });
    }
    await api.delete('/memories/${memory['id']}');
    await api.delete('/events/$id');
    stdout.writeln(
      'PASS: Flutter API real HTTP demo/dashboard/event/memory/chat/preferences/reminder flow.',
    );
  } finally {
    if (authenticated) await api.delete('/me');
    api.close();
  }
}
