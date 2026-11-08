import 'package:flutter/foundation.dart';
import 'api.dart';

class CompanionState extends ChangeNotifier {
  CompanionState(this.api, this.user);
  final CompanionApi api;
  Map<String, dynamic> user;
  Map<String, dynamic> dashboard = {}, preferences = {}, integrations = {};
  List<Map<String, dynamic>> events = [],
      memories = [],
      messages = [],
      reminders = [];
  bool loading = false;
  String? error;
  String? syncWarning;
  bool _disposed = false;

  void inspectResult(dynamic response) {
    if (response is! Map) {
      return;
    }
    if (response['memory_sync'] == 'failed') {
      syncWarning = '本地更改已保存；外部记忆同步或清理待重试，尚未确认外部完成。';
    } else if (['synced', 'cleared'].contains(response['memory_sync'])) {
      syncWarning = null;
    }
  }

  Future<void> refresh() async {
    if (loading || _disposed) {
      return;
    }
    loading = true;
    error = null;
    notifyListeners();
    try {
      final results = await Future.wait([
        api.get('/dashboard'),
        api.get('/events'),
        api.get('/memories'),
        api.get('/chat'),
        api.get('/reminders'),
        api.get('/preferences'),
        api.get('/integrations/status'),
      ]);
      // Commit a complete snapshot; don't silently mix accounts or time ranges.
      final nextEvents = decodeItems(results[1]);
      final nextMemories = decodeItems(results[2]);
      final nextMessages = decodeItems(results[3]);
      final nextReminders = decodeItems(results[4]);
      dashboard = results[0];
      user = (dashboard['user'] as Map<String, dynamic>?) ?? user;
      events = nextEvents;
      memories = nextMemories;
      messages = nextMessages;
      reminders = nextReminders;
      preferences = results[5];
      integrations = results[6];
    } catch (e) {
      error = e.toString();
    } finally {
      loading = false;
      if (!_disposed) {
        notifyListeners();
      }
    }
  }

  Future<void> mutate(Future<dynamic> Function() action) async {
    final response = await action();
    inspectResult(response);
    await refresh();
    if (error != null) throw ApiException('操作已保存，但刷新失败：$error');
  }

  @override
  void dispose() {
    _disposed = true;
    api.close();
    super.dispose();
  }
}
