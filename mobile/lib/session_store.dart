import 'package:flutter_secure_storage/flutter_secure_storage.dart';

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
