import 'dart:math';
import 'package:flutter/foundation.dart';
import 'api.dart';

/// Account-scoped care inbox. It never generates a greeting on the device.
class CareState extends ChangeNotifier {
  CareState(this.api);
  final CompanionApi api;
  List<Map<String, dynamic>> threads = [];
  Map<String, dynamic> status = {}, preferences = {};
  int unreadCount = 0;
  bool available = true, loading = false, _disposed = false;
  String? error;
  final Map<String, Map<String, dynamic>> _details = {};
  final Map<String, Map<String, dynamic>> _attempts = {};
  final Map<String, String> _drafts = {}, detailErrors = {};
  final Map<String, int> _versions = {};
  final Set<String> replying = {}, opening = {};
  int _listVersion = 0;

  static Map<String, dynamic>? _source(dynamic value) {
    if (value is! Map || value.length != 3) return null;
    final source = Map<String, dynamic>.from(value);
    if (source.keys.toSet().difference({'id', 'title', 'revision'}).isNotEmpty ||
        source['id'] is! String || (source['id'] as String).isEmpty ||
        source['title'] is! String || (source['title'] as String).isEmpty ||
        source['revision'] is! int || (source['revision'] as int) < 1) {
      return null;
    }
    return source;
  }

  static Map<String, dynamic> _thread(Map<String, dynamic> value) =>
      {...value, 'source_thread': _source(value['source_thread'])};

  bool get enabled => status.containsKey('enabled')
      ? status['enabled'] == true
      : preferences['enabled'] == true;
  String draft(String id) => _drafts[id] ?? '';
  void setDraft(String id, String value) => _drafts[id] = value;
  Map<String, dynamic>? detail(String id) => _details[id];
  List<Map<String, dynamic>> messages(String id) =>
      (_details[id]?['messages'] as List?)?.map(decodeObject).toList() ?? [];
  Map<String, dynamic>? pendingReply(String id) {
    for (final message in messages(id).reversed) {
      if (message['role'] == 'user') {
        if (message['reply_status'] == 'pending' &&
            message['request_id'] is String) {
          return message;
        }
        break;
      }
    }
    return _attempts[id];
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  Future<void> refresh() async {
    if (_disposed || loading) return;
    loading = true;
    final version = ++_listVersion;
    _notify();
    try {
      final value = await api.get('/care');
      final rows = decodeItems(value);
      if (_disposed || version != _listVersion) return;
      threads = rows.map(_thread).toList();
      unreadCount = value['unread_count'] is int
          ? value['unread_count'] as int
          : 0;
      status = value['status'] is Map
          ? Map<String, dynamic>.from(value['status'])
          : {};
      available = true;
      error = null;
    } catch (e) {
      if (!_disposed && version == _listVersion) {
        error = e is ApiException && e.statusCode == 404
            ? '当前服务尚未支持主动关心，请更新服务后再试。'
            : '关心消息暂时无法刷新，可以稍后重试。';
        if (e is ApiException && e.statusCode == 404) available = false;
      }
    } finally {
      if (!_disposed && version == _listVersion) {
        loading = false;
        _notify();
      }
    }
  }

  Future<void> setEnabled(bool enabled, {bool consentConfirmed = false}) async {
    if (enabled && !consentConfirmed) {
      throw const ApiException('开启前需要明确允许 AI 使用你的必要摘要判断是否需要主动关心。');
    }
    if (enabled) await api.patch('/preferences', {'model_consent': true});
    final value = await api.patch('/care/preferences', {'enabled': enabled});
    if (_disposed) return;
    preferences = value;
    status = {...status, 'enabled': enabled};
    await refresh();
  }

  Future<void> open(String id, {bool silent = false}) async {
    if (_disposed ||
        (silent && (replying.contains(id) || opening.contains(id)))) {
      return;
    }
    final version = (_versions[id] ?? 0) + 1;
    _versions[id] = version;
    if (!silent) {
      opening.add(id);
      detailErrors.remove(id);
      _notify();
    }
    try {
      final value = await api.get('/care/${Uri.encodeComponent(id)}');
      if (_disposed || _versions[id] != version) return;
      _validateDetail(value, id);
      value['thread'] = _thread(decodeObject(value['thread']));
      _details[id] = value;
      detailErrors.remove(id);
      if (value['reply_status'] == 'complete') _attempts.remove(id);
      await markRead(id);
    } catch (e) {
      if (!_disposed && _versions[id] == version) {
        detailErrors[id] = e.toString();
      }
    } finally {
      if (!_disposed && _versions[id] == version) {
        opening.remove(id);
        _notify();
      }
    }
  }

  void _validateDetail(Map<String, dynamic> value, String id) {
    if (value['thread'] is! Map ||
        (value['thread'] as Map)['id'] != id ||
        value['messages'] is! List) {
      throw const ApiException('关心会话格式不正确，请刷新后重试。');
    }
    for (final row in value['messages'] as List) {
      final message = decodeObject(row);
      if (!['assistant', 'user'].contains(message['role']) ||
          message['content'] is! String) {
        throw const ApiException('关心消息格式不正确。');
      }
    }
  }

  Future<void> markRead(String id) async {
    await api.post('/care/${Uri.encodeComponent(id)}/read', {});
    if (_disposed) return;
    var changed = false;
    threads = threads.map((thread) {
      if (thread['id'] == id && thread['unread'] == true) {
        changed = true;
        return {...thread, 'unread': false};
      }
      return thread;
    }).toList();
    if (changed) unreadCount = max(0, unreadCount - 1);
    _notify();
  }

  String _requestId() {
    final random = Random.secure();
    return List.generate(
      16,
      (_) => random.nextInt(256).toRadixString(16).padLeft(2, '0'),
    ).join();
  }

  Future<void> retryReply(String id) async {
    final pending = pendingReply(id);
    if (pending == null) return;
    await reply(id, pending['content'] as String);
  }

  Future<void> reply(String id, String message) async {
    final content = message.trim();
    if (_disposed || replying.contains(id)) return;
    if (content.isEmpty || content.length > 2000) {
      throw const ApiException('请输入1到2000字的回复。');
    }
    final pending = pendingReply(id);
    if (pending != null && pending['content'] != content) {
      throw const ApiException('上一条回复已保存，请先重试小伴的回应；新草稿会保留。');
    }
    final requestId = pending?['request_id'] as String? ?? _requestId();
    _attempts[id] = {
      'content': content,
      'request_id': requestId,
      'reply_status': 'pending',
      'delivery_unknown': true,
    };
    replying.add(id);
    detailErrors.remove(id);
    _versions[id] = (_versions[id] ?? 0) + 1;
    _notify();
    try {
      final value = await api.post('/care/${Uri.encodeComponent(id)}/reply', {
        'message': content,
        'request_id': requestId,
      });
      if (_disposed) return;
      _validateDetail(value, id);
      value['thread'] = _thread(decodeObject(value['thread']));
      _details[id] = value;
      if (value['reply_status'] == 'complete') _attempts.remove(id);
      if (_drafts[id]?.trim() == content) _drafts[id] = '';
      await markRead(id);
      await refresh();
    } catch (e) {
      if (!_disposed) detailErrors[id] = e.toString();
      rethrow;
    } finally {
      if (!_disposed) {
        replying.remove(id);
        _notify();
      }
    }
  }

  @override
  void dispose() {
    _disposed = true;
    ++_listVersion;
    _details.clear();
    _drafts.clear();
    _attempts.clear();
    super.dispose();
  }
}

class CareAdminService {
  CareAdminService(this.api, {required this.isAdmin});
  final CompanionApi api;
  final bool Function() isAdmin;
  void _authorize() {
    if (!isAdmin()) throw const ApiException('仅管理员可以修改服务配置。');
  }

  Future<Map<String, dynamic>> load() async {
    _authorize();
    final values = await Future.wait([
      api.get('/admin/care-config'),
      api.get('/admin/push-config'),
      api.get('/admin/model-config'),
    ]);
    _authorize();
    return {'care': values[0], 'push': values[1], 'model': values[2]};
  }

  Future<Map<String, dynamic>> saveCare(Map<String, dynamic> value) async {
    _authorize();
    return api.patch('/admin/care-config', value);
  }

  Future<Map<String, dynamic>> savePush(Map<String, dynamic> value) async {
    _authorize();
    final safe = {...value};
    if ((safe['master_secret'] as String?)?.trim().isEmpty == true) {
      safe.remove('master_secret');
    }
    return api.patch('/admin/push-config', safe);
  }

  Future<Map<String, dynamic>> saveModel(Map<String, dynamic> value) async {
    _authorize();
    final safe = {...value};
    for (final key in ['api_key', 'gateway_key']) {
      if ((safe[key] as String?)?.trim().isEmpty == true) safe.remove(key);
    }
    return api.patch('/admin/model-config', safe);
  }

  Future<Map<String, dynamic>> checkCare() async {
    _authorize();
    return api.post('/admin/care-check', {});
  }

  Future<Map<String, dynamic>> testPush() async {
    _authorize();
    return api.post('/admin/push-test', {});
  }
}
