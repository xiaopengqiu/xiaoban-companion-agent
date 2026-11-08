import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'api.dart';
import 'app_state.dart';
import 'auth_page.dart';
import 'screens.dart';
import 'services.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const CompanionApp());
}

class CompanionApp extends StatefulWidget {
  const CompanionApp({super.key});
  @override
  State<CompanionApp> createState() => _CompanionAppState();
}

class _CompanionAppState extends State<CompanionApp> {
  final store = const SessionStore();
  late final health = HealthSyncService(store);
  late final push = PushService(store);
  CompanionState? state;
  bool booting = true;
  String? bootError;
  String server = const String.fromEnvironment('COMPANION_URL');

  @override
  void initState() {
    super.initState();
    _restore();
  }

  Future<void> _restore() async {
    if (server.isEmpty) {
      server = Platform.isAndroid
          ? 'http://10.0.2.2:8765'
          : 'http://127.0.0.1:8765';
    }
    try {
      server = await store.read('server') ?? server;
      final token = await store.read('token');
      if (token != null) {
        final api = CompanionApi(server, requireHttps: kReleaseMode)
          ..token = token;
        try {
          final user = await api.get('/me');
          state = CompanionState(api, user);
        } catch (e) {
          api.close();
          // Preserve the saved session on transient network errors so the
          // user can retry without silently losing access to local settings.
          bootError = '暂时无法恢复会话：$e。可重新登录或体验独立演示。';
        }
      }
    } catch (e) {
      bootError = '无法读取安全存储：$e';
    }
    if (mounted) setState(() => booting = false);
  }

  Future<void> _authenticate(
    String url,
    String mode,
    Map<String, dynamic> data,
  ) async {
    final api = CompanionApi(url, requireHttps: kReleaseMode);
    try {
      final result = await api.post(
        mode == 'demo' ? '/demo' : '/auth/$mode',
        mode == 'demo' ? {} : data,
      );
      final token = result['token'];
      if (token is! String || token.isEmpty) {
        throw const ApiException('服务未返回登录会话。');
      }
      final user = decodeObject(result['user']);
      await health.disconnect();
      await push.disconnect();
      await store.write('server', api.baseUrl);
      await store.write('token', token);
      api.token = token;
      if (mounted) {
        setState(() {
          server = api.baseUrl;
          state = CompanionState(api, user);
          bootError = null;
        });
      }
    } catch (_) {
      api.close();
      rethrow;
    }
  }

  Future<void> _logout({bool deleteAccount = false}) async {
    // Stop native uploads before changing the authenticated identity.
    await health.disconnect();
    await push.disconnect();
    if (deleteAccount) await state!.api.delete('/me');
    if (!deleteAccount) {
      try {
        await state?.api.post('/auth/logout', {});
      } catch (_) {}
    }
    await store.delete('token');
    final previous = state;
    if (mounted) setState(() => state = null);
    previous?.dispose();
  }

  @override
  Widget build(BuildContext context) => MaterialApp(
    title: '小伴',
    debugShowCheckedModeBanner: false,
    locale: const Locale('zh', 'CN'),
    supportedLocales: const [Locale('zh', 'CN'), Locale('en')],
    localizationsDelegates: GlobalMaterialLocalizations.delegates,
    theme: ThemeData(
      useMaterial3: true,
      scaffoldBackgroundColor: const Color(0xFFF8F6EF),
      colorScheme: ColorScheme.fromSeed(
        seedColor: const Color(0xFF196B5C),
        primary: const Color(0xFF196B5C),
        surface: const Color(0xFFFFFEFA),
      ),
      appBarTheme: const AppBarTheme(
        backgroundColor: Color(0xFFF8F6EF),
        surfaceTintColor: Colors.transparent,
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: Colors.white,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide: const BorderSide(color: Color(0xFFDDE5DD)),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide: const BorderSide(color: Color(0xFFDDE5DD)),
        ),
      ),
      cardTheme: CardThemeData(
        elevation: 0,
        color: const Color(0xFFFFFEFA),
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(24),
          side: const BorderSide(color: Color(0xFFE3E8DE)),
        ),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(16),
          ),
        ),
      ),
    ),
    home: booting
        ? const Scaffold(body: Center(child: CircularProgressIndicator()))
        : state == null
        ? Column(
            children: [
              if (bootError != null)
                SafeArea(
                  bottom: false,
                  child: MaterialBanner(
                    content: Text(bootError!),
                    actions: [
                      TextButton(
                        onPressed: () => setState(() => bootError = null),
                        child: const Text('知道了'),
                      ),
                    ],
                  ),
                ),
              Expanded(
                child: AuthPage(initialServer: server, onSubmit: _authenticate),
              ),
            ],
          )
        : CompanionShell(
            key: ValueKey(state!.user['id']),
            state: state!,
            health: health,
            push: push,
            onLogout: _logout,
          ),
  );
}
