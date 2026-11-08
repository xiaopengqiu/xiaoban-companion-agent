import 'package:flutter/material.dart';
import 'api.dart';

typedef SubmitAuth =
    Future<void> Function(
      String server,
      String mode,
      Map<String, dynamic> data,
    );

class AuthPage extends StatefulWidget {
  const AuthPage({
    super.key,
    required this.initialServer,
    required this.onSubmit,
  });
  final String initialServer;
  final SubmitAuth onSubmit;
  @override
  State<AuthPage> createState() => _AuthPageState();
}

class _AuthPageState extends State<AuthPage> {
  final _form = GlobalKey<FormState>();
  late final _server = TextEditingController(text: widget.initialServer);
  final _name = TextEditingController();
  final _email = TextEditingController();
  final _password = TextEditingController();
  final _timezone = TextEditingController(text: 'Asia/Shanghai');
  bool _register = false, _consent = false, _busy = false;
  String? _error;
  @override
  void dispose() {
    for (final controller in [_server, _name, _email, _password, _timezone]) {
      controller.dispose();
    }
    super.dispose();
  }

  Future<void> _submit({bool demo = false}) async {
    if (_busy) return;
    try {
      final server = normalizeServerUrl(_server.text);
      if (!demo && !_form.currentState!.validate()) return;
      if (!demo && _register && !_consent) {
        setState(() => _error = '请先同意采集与处理你主动授权的健康数据。');
        return;
      }
      setState(() {
        _busy = true;
        _error = null;
      });
      await widget.onSubmit(
        server,
        demo ? 'demo' : (_register ? 'register' : 'login'),
        {
          'email': _email.text.trim(),
          'password': _password.text,
          if (_register) ...{
            'name': _name.text.trim(),
            'timezone': _timezone.text.trim(),
            'consent_health_data': _consent,
          },
        },
      );
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    body: SafeArea(
      child: Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 440),
            child: Form(
              key: _form,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const Icon(
                    Icons.spa_rounded,
                    size: 52,
                    color: Color(0xFF196B5C),
                  ),
                  const SizedBox(height: 20),
                  Text(
                    '小伴',
                    textAlign: TextAlign.center,
                    style: Theme.of(context).textTheme.headlineLarge?.copyWith(
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  const SizedBox(height: 8),
                  const Text('记住你的日常，陪你慢慢变好', textAlign: TextAlign.center),
                  const SizedBox(height: 32),
                  SegmentedButton<bool>(
                    segments: const [
                      ButtonSegment(value: false, label: Text('登录')),
                      ButtonSegment(value: true, label: Text('注册')),
                    ],
                    selected: {_register},
                    onSelectionChanged: _busy
                        ? null
                        : (values) => setState(() {
                            _register = values.first;
                            _error = null;
                          }),
                  ),
                  const SizedBox(height: 24),
                  if (_register) ...[
                    TextFormField(
                      key: const Key('name'),
                      controller: _name,
                      decoration: const InputDecoration(labelText: '怎么称呼你'),
                      validator: (value) =>
                          value == null || value.trim().isEmpty
                          ? '请输入称呼'
                          : null,
                    ),
                    const SizedBox(height: 16),
                  ],
                  TextFormField(
                    key: const Key('email'),
                    controller: _email,
                    keyboardType: TextInputType.emailAddress,
                    autofillHints: const [AutofillHints.email],
                    decoration: const InputDecoration(labelText: '邮箱'),
                    validator: (value) => value == null || !value.contains('@')
                        ? '请输入有效邮箱'
                        : null,
                  ),
                  const SizedBox(height: 16),
                  TextFormField(
                    key: const Key('password'),
                    controller: _password,
                    obscureText: true,
                    autofillHints: _register
                        ? const [AutofillHints.newPassword]
                        : const [AutofillHints.password],
                    decoration: InputDecoration(
                      labelText: _register ? '密码（至少 10 位）' : '密码',
                    ),
                    validator: (value) =>
                        value == null ||
                            value.isEmpty ||
                            (_register && value.length < 10)
                        ? (_register ? '密码至少 10 位' : '请输入密码')
                        : null,
                  ),
                  if (_register) ...[
                    const SizedBox(height: 16),
                    TextFormField(
                      controller: _timezone,
                      decoration: const InputDecoration(
                        labelText: '时区',
                        helperText: '例如 Asia/Shanghai，提醒会使用这个时区',
                      ),
                      validator: (value) =>
                          value == null || value.trim().isEmpty
                          ? '请输入 IANA 时区'
                          : null,
                    ),
                    CheckboxListTile(
                      contentPadding: EdgeInsets.zero,
                      value: _consent,
                      onChanged: (value) =>
                          setState(() => _consent = value ?? false),
                      title: const Text(
                        '我同意采集与处理我主动授权的健康数据',
                        style: TextStyle(fontSize: 13),
                      ),
                      subtitle: const Text(
                        '可查看、更正、删除记录与记忆，也可删除账号全部数据。',
                        style: TextStyle(fontSize: 12),
                      ),
                    ),
                  ],
                  ExpansionTile(
                    tilePadding: EdgeInsets.zero,
                    title: const Text('服务地址'),
                    subtitle: Text(
                      _server.text,
                      style: const TextStyle(fontSize: 12),
                    ),
                    children: [
                      TextField(
                        controller: _server,
                        autocorrect: false,
                        keyboardType: TextInputType.url,
                        decoration: const InputDecoration(
                          labelText: '陪伴服务 URL',
                          helperText: '真机填写电脑局域网 IP 或 HTTPS 域名',
                        ),
                      ),
                    ],
                  ),
                  if (_error != null)
                    Padding(
                      padding: const EdgeInsets.symmetric(vertical: 12),
                      child: Text(
                        _error!,
                        style: TextStyle(
                          color: Theme.of(context).colorScheme.error,
                        ),
                      ),
                    ),
                  const SizedBox(height: 16),
                  FilledButton(
                    key: const Key('submit-auth'),
                    onPressed: _busy ? null : () => _submit(),
                    child: Padding(
                      padding: const EdgeInsets.all(13),
                      child: _busy
                          ? const SizedBox(
                              height: 20,
                              width: 20,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : Text(_register ? '开始陪伴' : '回到小伴'),
                    ),
                  ),
                  const SizedBox(height: 12),
                  OutlinedButton(
                    onPressed: _busy ? null : () => _submit(demo: true),
                    child: const Padding(
                      padding: EdgeInsets.all(12),
                      child: Text('体验独立演示'),
                    ),
                  ),
                  const SizedBox(height: 12),
                  const Text(
                    '演示会创建独立账号，使用明确标注的合成数据。\n本应用用于日常健康陪伴，不提供医疗诊断。',
                    textAlign: TextAlign.center,
                    style: TextStyle(fontSize: 12, color: Color(0xFF6A7771)),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    ),
  );
}
