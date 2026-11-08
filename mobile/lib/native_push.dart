import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter/services.dart';
import 'package:firebase_core/firebase_core.dart';
import 'package:jpush_flutter/jpush_flutter.dart';
import 'package:jpush_flutter/jpush_interface.dart';
import 'api.dart';
import 'session_store.dart';

class NativePushEvent {
  const NativePushEvent(this.type, this.payload);
  final String type;
  final Map<String, dynamic> payload;
}

abstract class NativePushDriver {
  Stream<NativePushEvent> get events;
  Future<bool> permission({required bool request});
  Future<Map<String, dynamic>> initialize(Map<String, dynamic> config);
  Future<String> registrationId();
  Future<Map<String, dynamic>> launchNotification();
  Future<void> stop();
}

/// Uses the pinned official plugin for callbacks; a narrow Android bridge applies
/// JPushConfig before initialization, including runtime Xiaomi/OPPO parameters.
class JPushNativeDriver implements NativePushDriver {
  static const channel = MethodChannel('xiaoban/native-push');
  final _events = StreamController<NativePushEvent>.broadcast();
  JPushFlutterInterface? _sdk;
  String? _appKey;
  JPushFlutterInterface get sdk => _sdk ??= JPush.newJPush();
  @override
  Stream<NativePushEvent> get events => _events.stream;
  @override
  Future<bool> permission({required bool request}) async {
    if (!Platform.isAndroid) throw const ApiException('此原生推送通道用于 Android。');
    return await channel.invokeMethod<bool>('notificationPermission', {
          'request': request,
        }) ??
        false;
  }

  @override
  Future<Map<String, dynamic>> initialize(Map<String, dynamic> config) async {
    final key = config['app_key'] as String;
    if (_appKey != null && _appKey != key) {
      throw const ApiException('管理员已更换推送应用，请重启小伴后重新开启通知。');
    }
    final options = Map<String, dynamic>.from(config['client_options'] as Map);
    final firebase = Map<String, dynamic>.from(
      options['firebase'] as Map? ?? {},
    );
    const firebaseKeys = [
      'api_key',
      'app_id',
      'messaging_sender_id',
      'project_id',
    ];
    if (firebaseKeys.every(
      (k) => firebase[k] is String && (firebase[k] as String).isNotEmpty,
    )) {
      if (Firebase.apps.isEmpty) {
        await Firebase.initializeApp(
          options: FirebaseOptions(
            apiKey: firebase['api_key'],
            appId: firebase['app_id'],
            messagingSenderId: firebase['messaging_sender_id'],
            projectId: firebase['project_id'],
          ),
        );
      } else if (Firebase.app().options.projectId != firebase['project_id'] ||
          Firebase.app().options.appId != firebase['app_id']) {
        throw const ApiException('FCM 客户端项目已变更，请重启应用；若包内已固定其他项目，需要重新打包。');
      }
    }
    Future<dynamic> emit(String type, Map<String, dynamic> event) async {
      _events.add(NativePushEvent(type, event));
    }

    sdk.addEventHandler(
      onReceiveNotification: (e) => emit('received', e),
      onOpenNotification: (e) => emit('opened', e),
      onReceiveMessage: (e) => emit('received', e),
      onConnected: (e) => emit('connected', e),
      onReceiveDeviceToken: (e) => emit('connected', e),
      onReceiveNotificationAuthorization: (e) => emit('permission', e),
      onNotifyMessageUnShow: (e) => emit('received', e),
      onInAppMessageClick: (e) => emit('received', e),
      onInAppMessageShow: (e) => emit('received', e),
      onNotifyButtonClick: (e) => emit('opened', e),
      onCommandResult: (e) => emit('command', e),
      onVoipMessage: (e) => emit('received', e),
    );
    final result =
        await channel.invokeMapMethod<String, dynamic>('initialize', {
          'app_key': key,
          'manufacturers': options['manufacturers'] ?? {},
        }) ??
        {};
    _appKey = key;
    return result;
  }

  @override
  Future<String> registrationId() =>
      sdk.getRegistrationID().timeout(const Duration(seconds: 25));
  @override
  Future<Map<String, dynamic>> launchNotification() async =>
      Map<String, dynamic>.from(await sdk.getLaunchAppNotification());
  @override
  Future<void> stop() async {
    if (_appKey == null) return;
    await sdk.stopPush();
    await sdk.clearAllNotifications();
    await channel.invokeMethod<void>('stop');
  }
}

String? careIdFromNotification(Map<String, dynamic> event) {
  String? inspect(dynamic value, int depth) {
    if (depth > 2) return null;
    if (value is String && value.length < 16000) {
      try {
        value = jsonDecode(value);
      } catch (_) {
        return null;
      }
    }
    if (value is! Map) return null;
    final id = value['care_id'];
    if (id is String && RegExp(r'^[A-Za-z0-9_-]{1,80}$').hasMatch(id)) {
      return id;
    }
    for (final key in ['extras', 'cn.jpush.android.EXTRA', 'data']) {
      final found = inspect(value[key], depth + 1);
      if (found != null) return found;
    }
    return null;
  }

  return inspect(event, 0);
}

class NativeCarePushService {
  NativeCarePushService(this.store, {NativePushDriver? driver})
    : _injected = driver;
  final SessionStore store;
  final NativePushDriver? _injected;
  NativePushDriver? _created;
  NativePushDriver get driver =>
      _injected ?? (_created ??= JPushNativeDriver());
  StreamSubscription<NativePushEvent>? _events;
  CompanionApi? _api;
  String? _registration;
  dynamic _revision;
  bool _active = false, _enabling = false;
  int? _registeringEpoch;
  int _epoch = 0;
  VoidCallback? _onMessage;
  ValueChanged<String>? _onOpen;
  final _opened = <String, DateTime>{};
  Map<String, dynamic>? _initialOpen;
  String status = '关心保存在应用内；系统通知尚未开启';

  Future<void> restore(
    CompanionApi api,
    VoidCallback onMessage, {
    ValueChanged<String>? onOpenCare,
  }) async {
    if (await store.read('native_push_registered') != 'true' &&
        await store.read('native_push_opted_in') != 'true') {
      return;
    }
    try {
      await enable(
        api,
        onMessage,
        onOpenCare: onOpenCare,
        askPermission: false,
      );
    } catch (e) {
      status = '系统通知暂未恢复：$e';
    }
  }

  Map<String, dynamic> _public(Map<String, dynamic> source) {
    const firebaseKeys = [
      'api_key',
      'app_id',
      'messaging_sender_id',
      'project_id',
    ];
    const vendorKeys = [
      'huawei_app_id',
      'xiaomi_app_id',
      'xiaomi_app_key',
      'oppo_app_key',
      'oppo_app_secret',
      'vivo_app_id',
      'vivo_app_key',
      'honor_app_id',
    ];
    final sourceOptions = source['client_options'] is Map
        ? source['client_options'] as Map
        : {};
    Map<String, dynamic> pick(dynamic values, List<String> keys) => {
      for (final key in keys)
        if (values is Map && values[key] is String) key: values[key],
    };
    return {
      'app_key': source['app_key'],
      'client_options': {
        'firebase': pick(sourceOptions['firebase'], firebaseKeys),
        'manufacturers': pick(sourceOptions['manufacturers'], vendorKeys),
      },
    };
  }

  Future<String> enable(
    CompanionApi api,
    VoidCallback onMessage, {
    ValueChanged<String>? onOpenCare,
    bool askPermission = true,
  }) async {
    if (_enabling) throw const ApiException('系统通知正在注册，请稍候。');
    _enabling = true;
    try {
      return await _enable(
        api,
        onMessage,
        onOpenCare: onOpenCare,
        askPermission: askPermission,
      );
    } finally {
      _enabling = false;
    }
  }

  Future<String> _enable(
    CompanionApi api,
    VoidCallback onMessage, {
    ValueChanged<String>? onOpenCare,
    required bool askPermission,
  }) async {
    final epoch = ++_epoch;
    _active = false;
    _initialOpen = null;
    final config = await api.get('/push/native-config');
    if (config['provider'] != 'jpush' ||
        config['configured'] != true ||
        config['enabled'] != true ||
        config['app_key'] is! String ||
        (config['app_key'] as String).isEmpty) {
      throw const ApiException('系统通知服务尚未配置，主动关心仍可在应用内查看。');
    }
    if (epoch != _epoch) throw const ApiException('账号已切换，已取消开启通知。');
    if (!await driver.permission(request: askPermission)) {
      throw const ApiException('通知权限未开启，主动关心仍可在应用内查看。');
    }
    if (epoch != _epoch) throw const ApiException('账号已切换，已取消开启通知。');
    await store.write('native_push_opted_in', 'true');
    await _checkPersistedEpoch(epoch);
    _onMessage = onMessage;
    _onOpen = onOpenCare;
    _api = api;
    _revision = config['revision'];
    await _events?.cancel();
    _events = driver.events.listen((event) {
      if (epoch != _epoch) return;
      if (event.type == 'opened') {
        if (_active) {
          _open(event.payload);
        } else {
          _initialOpen = event.payload;
        }
      } else if (event.type == 'received' && _active) {
        _onMessage?.call();
      } else if (event.type == 'connected' && _active) {
        unawaited(_register(epoch));
      }
    });
    try {
      final readiness = await driver.initialize(_public(config));
      if (epoch != _epoch) {
        await driver.stop();
        throw const ApiException('账号已切换，已取消设备注册。');
      }
      await _register(epoch, initial: true);
      if (epoch != _epoch) throw const ApiException('账号已切换，已取消设备注册。');
      _active = true;
      await store.write('native_push_registered', 'true');
      await _checkPersistedEpoch(epoch);
      status = readiness['rebuild_required'] == true
          ? '基础通知已注册；部分厂商离线通道参数需管理员重新打包后生效。'
          : '系统通知已注册。实际送达还受免费通道、厂商权限和设备设置影响。';
      if (_initialOpen != null) _open(_initialOpen!);
      _initialOpen = null;
      _open(await driver.launchNotification());
      return status;
    } catch (e) {
      if (epoch == _epoch) {
        status = '系统通知尚未完成注册：$e';
        _active = false;
      }
      rethrow;
    }
  }

  Future<void> _checkPersistedEpoch(int epoch) async {
    if (epoch == _epoch) return;
    // enable remains locked until this returns, so a newer account cannot yet
    // have written its own preferences. Undo a storage completion after logout.
    await store.delete('native_push_registered');
    await store.delete('native_push_opted_in');
    throw const ApiException('账号已切换，已取消开启通知。');
  }

  void _open(Map<String, dynamic> payload) {
    final id = careIdFromNotification(payload);
    if (id == null) return;
    final now = DateTime.now();
    _opened.removeWhere(
      (_, at) => now.difference(at) > const Duration(seconds: 30),
    );
    if (_opened.containsKey(id)) return;
    if (_opened.length >= 100) _opened.clear();
    _opened[id] = now;
    _onOpen?.call(id);
  }

  Future<void> _register(int epoch, {bool initial = false}) async {
    if (_registeringEpoch == epoch) return;
    _registeringEpoch = epoch;
    try {
      final rid = await driver.registrationId();
      if (epoch != _epoch) return;
      if (rid.isEmpty) throw const ApiException('尚未取得设备推送标识，请稍后重新开启。');
      if (!initial && rid == _registration) return;
      final api = _api, old = _registration;
      if (api == null) return;
      await api.post('/push/devices', {
        'provider': 'jpush',
        'registration_id': rid,
        'config_revision': _revision,
      });
      if (epoch != _epoch) {
        // Logout may finish while the registration request is in flight.
        // The captured account can only remove its own binding on the server.
        try {
          await api.delete('/push/devices', {'registration_id': rid});
        } catch (_) {}
        return;
      }
      _registration = rid;
      if (old != null && old != rid) {
        try {
          await api.delete('/push/devices', {'registration_id': old});
        } catch (_) {}
      }
    } catch (e) {
      if (initial) rethrow;
      if (epoch == _epoch) status = '设备标识更新失败，请稍后重新开启系统通知。';
    } finally {
      if (_registeringEpoch == epoch) _registeringEpoch = null;
    }
  }

  Future<void> disconnect() async {
    ++_epoch;
    _active = false;
    await _events?.cancel();
    _events = null;
    final api = _api, rid = _registration;
    _api = null;
    _registration = null;
    _onMessage = null;
    _onOpen = null;
    _opened.clear();
    _initialOpen = null;
    if (api != null && rid != null) {
      try {
        await api.delete('/push/devices', {'registration_id': rid});
      } catch (_) {}
    }
    if (_injected != null || _created != null) {
      try {
        await driver.stop();
      } catch (_) {}
    }
    await store.delete('native_push_registered');
    await store.delete('native_push_opted_in');
    status = '系统通知已关闭，应用内关心仍会保留';
  }
}
