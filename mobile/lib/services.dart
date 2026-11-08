import 'dart:async';
import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:open_wearables_health_sdk/open_wearables_health_sdk.dart';
import 'package:open_wearables_health_sdk/health_data_type.dart';
import 'api.dart';

class SessionStore {
  const SessionStore();
  static const _storage = FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
    iOptions: IOSOptions(
      accessibility: KeychainAccessibility.first_unlock_this_device,
    ),
  );
  Future<String?> read(String key) => _storage.read(key: 'companion_$key');
  Future<void> write(String key, String value) =>
      _storage.write(key: 'companion_$key', value: value);
  Future<void> delete(String key) => _storage.delete(key: 'companion_$key');
}

class HealthSyncService {
  HealthSyncService(this.store);
  final SessionStore store;
  String status = '尚未连接健康数据';
  Future<String> connect(CompanionApi api) async {
    if (!Platform.isAndroid && !Platform.isIOS) {
      throw const ApiException('健康数据同步仅支持 iOS 或 Android 手机。');
    }
    final credentials = WearableConnection.fromJson(
      await api.post('/integrations/open-wearables/connect', {}),
    );
    normalizeServerUrl(credentials.host, requireHttps: kReleaseMode);
    await OpenWearablesHealthSdk.setLogLevel(OWLogLevel.none);
    await OpenWearablesHealthSdk.configure(host: credentials.host);
    await store.write('health_host', credentials.host);
    // configure may restore a previous native session. Replace it with the
    // server-bound identity rather than accepting an arbitrary local user ID.
    await OpenWearablesHealthSdk.signOut();
    try {
      await OpenWearablesHealthSdk.signIn(
        userId: credentials.userId,
        accessToken: credentials.accessToken,
        refreshToken: credentials.refreshToken,
      );
      if (Platform.isAndroid) {
        final providers = await OpenWearablesHealthSdk.getAvailableProviders();
        if (providers.isEmpty) {
          throw const ApiException(
            '未找到健康数据提供方。请安装 Health Connect 或启用 Samsung Health 后重试。',
          );
        }
        final preferred = providers.where(
          (p) => p.provider == AndroidHealthProvider.healthConnect,
        );
        final provider =
            (preferred.isNotEmpty ? preferred.first : providers.first).provider;
        if (provider == null) throw const ApiException('手机的健康数据提供方暂不受支持。');
        await OpenWearablesHealthSdk.setProvider(provider);
      }
      final allowed = await OpenWearablesHealthSdk.requestAuthorization(
        types: const [
          HealthDataType.steps,
          HealthDataType.sleep,
          HealthDataType.workout,
          HealthDataType.dietaryWater,
          HealthDataType.heartRate,
        ],
      );
      if (!allowed) throw const ApiException('尚未授予健康读取权限，未启动同步。可在系统设置里调整权限。');
      if (Platform.isAndroid) {
        await OpenWearablesHealthSdk.setSyncNotification(
          title: '小伴 · 健康同步',
          text: '正在同步你已授权的健康记录',
        );
      }
      final started = await OpenWearablesHealthSdk.startBackgroundSync(
        syncDaysBack: 30,
      );
      if (!started) throw const ApiException('健康同步未能启动，请检查系统权限后重试。');
      status = '健康授权流程已完成，后台同步已启动；实际读取以系统授权和可用数据为准，首次导入请稍后刷新。';
      return status;
    } catch (_) {
      await OpenWearablesHealthSdk.signOut();
      status = '健康数据未连接';
      rethrow;
    }
  }

  Future<void> disconnect() async {
    final host = await store.read('health_host');
    if (host == null) return;
    if (!OpenWearablesHealthSdk.isConfigured) {
      await OpenWearablesHealthSdk.setLogLevel(OWLogLevel.none);
      await OpenWearablesHealthSdk.configure(host: host);
    }
    await OpenWearablesHealthSdk.stopBackgroundSync();
    await OpenWearablesHealthSdk.signOut();
    await store.delete('health_host');
    status = '健康同步已断开';
  }
}

class PushService {
  PushService(this.store);
  final SessionStore store;
  static const enabledInBuild = bool.fromEnvironment(
    'ENABLE_PUSH',
    defaultValue: false,
  );
  StreamSubscription<String>? _tokenUpdates;
  StreamSubscription<RemoteMessage>? _messages;
  bool _active = false;
  String status = '提醒保存在应用收件箱；系统推送尚未开启';

  Future<void> restore(CompanionApi api, VoidCallback onMessage) async {
    if (!enabledInBuild || await store.read('push_registered') != 'true') {
      return;
    }
    try {
      await enable(api, onMessage, askPermission: false);
    } catch (e) {
      status = '系统推送恢复失败：$e';
    }
  }

  Future<String> enable(
    CompanionApi api,
    VoidCallback onMessage, {
    bool askPermission = true,
  }) async {
    if (!enabledInBuild) {
      throw const ApiException(
        '此构建未配置系统推送。添加 Firebase 配置并用 ENABLE_PUSH=true 构建后可开启。',
      );
    }
    final integrations = await api.get('/integrations/status');
    if ((integrations['push'] as Map?)?['configured'] != true) {
      throw const ApiException('服务端未配置推送凭证；提醒仍会保存在收件箱。');
    }
    try {
      if (Firebase.apps.isEmpty) await Firebase.initializeApp();
    } catch (_) {
      throw const ApiException('Firebase 原生配置缺失或无效，系统推送未开启。');
    }
    final messaging = FirebaseMessaging.instance;
    final permission = askPermission
        ? await messaging.requestPermission()
        : await messaging.getNotificationSettings();
    if (![
      AuthorizationStatus.authorized,
      AuthorizationStatus.provisional,
    ].contains(permission.authorizationStatus)) {
      throw const ApiException('通知权限未开启；提醒仍可在收件箱查看。');
    }
    final token = await messaging.getToken();
    if (token == null || token.isEmpty) {
      throw const ApiException('尚未取得推送令牌，请在已配置推送的真机上稍后重试。');
    }
    Future<void> register(String token) async {
      await api.post('/devices', {
        'token': token,
        'platform': Platform.isAndroid ? 'android' : 'ios',
      });
    }

    await register(token);
    await store.write('push_registered', 'true');
    await _tokenUpdates?.cancel();
    _tokenUpdates = messaging.onTokenRefresh.listen((token) async {
      try {
        await register(token);
      } catch (_) {
        status = '推送令牌更新失败，请重新开启推送';
        onMessage();
      }
    });
    await _messages?.cancel();
    _messages = FirebaseMessaging.onMessage.listen((_) => onMessage());
    await messaging.setForegroundNotificationPresentationOptions(
      alert: true,
      badge: true,
      sound: true,
    );
    _active = true;
    status = '设备已注册推送。实际送达取决于服务、系统权限和网络。';
    return status;
  }

  Future<void> disconnect() async {
    await _tokenUpdates?.cancel();
    await _messages?.cancel();
    // Invalidate any previous owner's native token on this shared device.
    final previouslyRegistered = await store.read('push_registered') == 'true';
    if (previouslyRegistered && Firebase.apps.isEmpty) {
      await Firebase.initializeApp();
    }
    if (_active ||
        previouslyRegistered ||
        (enabledInBuild && Firebase.apps.isNotEmpty)) {
      await FirebaseMessaging.instance.deleteToken();
    }
    await store.delete('push_registered');
    _active = false;
    status = '系统推送已断开';
  }
}
