import 'dart:convert';
import 'dart:async';
import 'package:flutter/material.dart';
import 'api.dart';
import 'app_state.dart';
import 'care_state.dart';

String _when(dynamic value) {
  final date = DateTime.tryParse(value?.toString() ?? '')?.toLocal();
  if (date == null) return '';
  return '${date.month}月${date.day}日 ${date.hour.toString().padLeft(2, '0')}:${date.minute.toString().padLeft(2, '0')}';
}

Widget _card(Widget child) => Card(
  child: Padding(padding: const EdgeInsets.all(18), child: child),
);

class CareInboxPage extends StatelessWidget {
  const CareInboxPage({
    super.key,
    required this.care,
    this.afterPreferenceChange,
  });
  final CareState care;
  final Future<void> Function()? afterPreferenceChange;
  Future<void> _enable(BuildContext context) async {
    var consent = !care.enabled;
    if (consent) {
      consent =
          await showDialog<bool>(
            context: context,
            builder: (c) => AlertDialog(
              title: const Text('允许 AI 主动关心你？'),
              content: const Text(
                '小伴将使用你已授权的近期记录、记忆和在意的事情，定期判断是否值得联系你。没有需要关心的事项就保持安静，并遵守免打扰和频次限制。同意后允许模型处理必要的个人摘要，用于聊天与主动关心。',
              ),
              actions: [
                TextButton(
                  onPressed: () => Navigator.pop(c, false),
                  child: const Text('暂不开启'),
                ),
                FilledButton(
                  onPressed: () => Navigator.pop(c, true),
                  child: const Text('同意并开启'),
                ),
              ],
            ),
          ) ??
          false;
      if (!consent) return;
    }
    try {
      await care.setEnabled(!care.enabled, consentConfirmed: consent);
      await afterPreferenceChange?.call();
    } catch (e) {
      if (context.mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(e.toString())));
      }
    }
  }

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
    animation: care,
    builder: (context, _) => RefreshIndicator(
      onRefresh: care.refresh,
      child: ListView(
        padding: const EdgeInsets.all(18),
        physics: const AlwaysScrollableScrollPhysics(),
        children: [
          _card(
            Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '需要时，主动陪你聊聊',
                  style: Theme.of(context).textTheme.titleLarge,
                ),
                const SizedBox(height: 9),
                const Text('AI 判断没有需要注意的事时，不会发消息。你可以随时停止主动关心，已有会话会保留。'),
                if (care.status['available'] == false && care.enabled)
                  const Padding(
                    padding: EdgeInsets.only(top: 9),
                    child: Text('主动关心暂未就绪，已有消息仍可查看。'),
                  ),
                if (care.status['next_check_at'] != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 9),
                    child: Text(
                      '下次分析约在 ${_when(care.status['next_check_at'])}',
                    ),
                  ),
                const SizedBox(height: 12),
                OutlinedButton.icon(
                  onPressed: care.loading || !care.available
                      ? null
                      : () => _enable(context),
                  icon: Icon(
                    care.enabled
                        ? Icons.pause_circle_outline
                        : Icons.favorite_border,
                  ),
                  label: Text(care.enabled ? '停止主动关心' : '开启主动关心'),
                ),
              ],
            ),
          ),
          if (care.error != null)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 12),
              child: Text(
                care.error!,
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
            ),
          if (care.loading) const LinearProgressIndicator(),
          const SizedBox(height: 18),
          if (care.threads.isEmpty)
            _card(
              const Column(
                children: [
                  Icon(Icons.mark_chat_unread_outlined, size: 32),
                  SizedBox(height: 10),
                  Text('暂时没有主动关心'),
                  SizedBox(height: 6),
                  Text(
                    '小伴会在确实需要时联系你，不必为了保持联系而打扰。',
                    textAlign: TextAlign.center,
                  ),
                ],
              ),
            ),
          ...care.threads.map(
            (thread) => Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: Card(
                child: ListTile(
                  contentPadding: const EdgeInsets.symmetric(
                    horizontal: 18,
                    vertical: 10,
                  ),
                  leading: Badge(
                    isLabelVisible: thread['unread'] == true,
                    child: const Icon(Icons.favorite_outline_rounded),
                  ),
                  title: Text(
                    (thread['title'] ?? '小伴的关心').toString(),
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                  ),
                  subtitle: Padding(
                    padding: const EdgeInsets.only(top: 7),
                    child: Text(
                      '${thread['preview'] ?? ''}\n${_when(thread['updated_at'] ?? thread['created_at'])}',
                      maxLines: 3,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () => Navigator.push(
                    context,
                    MaterialPageRoute<void>(
                      builder: (_) => CareConversationPage(
                        care: care,
                        id: thread['id'].toString(),
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ),
        ],
      ),
    ),
  );
}

class CareConversationPage extends StatefulWidget {
  const CareConversationPage({super.key, required this.care, required this.id});
  final CareState care;
  final String id;
  @override
  State<CareConversationPage> createState() => _CareConversationPageState();
}

class _CareConversationPageState extends State<CareConversationPage>
    with WidgetsBindingObserver {
  late final TextEditingController input;
  Timer? poll;
  bool active = true;
  @override
  void initState() {
    super.initState();
    input = TextEditingController(text: widget.care.draft(widget.id));
    WidgetsBinding.instance.addObserver(this);
    poll = Timer.periodic(const Duration(seconds: 30), (_) {
      if (mounted && active && ModalRoute.of(context)?.isCurrent == true) {
        unawaited(widget.care.open(widget.id, silent: true));
      }
    });
    Future.microtask(() => widget.care.open(widget.id));
  }

  @override
  void dispose() {
    poll?.cancel();
    WidgetsBinding.instance.removeObserver(this);
    input.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState value) {
    active = value == AppLifecycleState.resumed;
    if (active && mounted && ModalRoute.of(context)?.isCurrent == true) {
      unawaited(widget.care.open(widget.id, silent: true));
    }
  }

  Future<void> _send({bool retry = false}) async {
    final message = input.text.trim();
    try {
      if (retry) {
        await widget.care.retryReply(widget.id);
      } else {
        await widget.care.reply(widget.id, message);
      }
      if (mounted && input.text.trim() == message) {
        input.text = widget.care.draft(widget.id);
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(e.toString())));
      }
    }
  }

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
    animation: widget.care,
    builder: (context, _) {
      final care = widget.care,
          id = widget.id,
          detail = care.detail(id),
          pending = care.pendingReply(id),
          busy = care.replying.contains(id);
      return Scaffold(
        appBar: AppBar(
          title: Text(
            (detail?['thread']?['title'] ?? '小伴的关心').toString(),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
        ),
        body: SafeArea(
          child: Column(
            children: [
              if (care.opening.contains(id) || busy)
                const LinearProgressIndicator(minHeight: 2),
              if (care.detailErrors[id] != null)
                MaterialBanner(
                  content: Text(care.detailErrors[id]!),
                  actions: [
                    TextButton(
                      onPressed: () => care.open(id),
                      child: const Text('刷新'),
                    ),
                  ],
                ),
              Expanded(
                child: ListView(
                  padding: const EdgeInsets.all(18),
                  children: [
                    const Text('小伴会接着这条关心与你交流。', textAlign: TextAlign.center),
                    const SizedBox(height: 18),
                    ...care
                        .messages(id)
                        .map(
                          (m) => Align(
                            alignment: m['role'] == 'user'
                                ? Alignment.centerRight
                                : Alignment.centerLeft,
                            child: Container(
                              constraints: BoxConstraints(
                                maxWidth:
                                    MediaQuery.sizeOf(context).width * .84,
                              ),
                              margin: const EdgeInsets.only(bottom: 14),
                              padding: const EdgeInsets.all(15),
                              decoration: BoxDecoration(
                                color: m['role'] == 'user'
                                    ? const Color(0xFFDCEBE3)
                                    : Colors.white,
                                borderRadius: BorderRadius.circular(18),
                              ),
                              child: SelectableText(m['content'] as String),
                            ),
                          ),
                        ),
                  ],
                ),
              ),
              if (pending != null)
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 18),
                  child: _card(
                    Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          pending['delivery_unknown'] == true
                              ? '回复状态待确认，重试会复用同一条请求。'
                              : '你的回复已保存，小伴暂时未能回应。',
                        ),
                        TextButton(
                          onPressed: busy ? null : () => _send(retry: true),
                          child: const Text('重试小伴回应'),
                        ),
                      ],
                    ),
                  ),
                ),
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 10, 16, 14),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.end,
                  children: [
                    Expanded(
                      child: TextField(
                        key: const Key('care-reply-input'),
                        controller: input,
                        maxLength: 2000,
                        minLines: 1,
                        maxLines: 4,
                        onChanged: (value) => care.setDraft(id, value),
                        decoration: const InputDecoration(
                          labelText: '回复这条关心',
                          counterText: '',
                          hintText: '说说你现在的感受…',
                        ),
                      ),
                    ),
                    const SizedBox(width: 8),
                    IconButton.filled(
                      key: const Key('care-reply-send'),
                      onPressed: busy || pending != null ? null : () => _send(),
                      tooltip: '发送回复',
                      icon: const Icon(Icons.send_rounded),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      );
    },
  );
}

class CareAdminPage extends StatefulWidget {
  const CareAdminPage({super.key, required this.state});
  final CompanionState state;
  @override
  State<CareAdminPage> createState() => _CareAdminPageState();
}

class _CareAdminPageState extends State<CareAdminPage> {
  late final service = CareAdminService(
    widget.state.api,
    isAdmin: () =>
        widget.state.user['is_admin'] == true &&
        widget.state.user['is_demo'] != true,
  );
  final fields = <String, TextEditingController>{};
  Map<String, dynamic>? values;
  bool working = false,
      careEnabled = false,
      pushEnabled = false,
      clearPushSecret = false;
  String mode = 'disabled', provider = 'disabled', localProvider = 'codex';
  String? error, notice;
  TextEditingController field(String name) =>
      fields.putIfAbsent(name, () => TextEditingController());
  @override
  void initState() {
    super.initState();
    Future.microtask(_load);
  }

  @override
  void dispose() {
    for (final c in fields.values) {
      c.clear();
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _load() async {
    if (widget.state.user['is_admin'] != true) {
      setState(() => error = '仅管理员可以管理服务配置。');
      return;
    }
    await _run(() async {
      final next = await service.load();
      if (!mounted) return;
      values = next;
      final care = next['care'] as Map,
          push = next['push'] as Map,
          model = next['model'] as Map;
      careEnabled = care['enabled'] == true;
      pushEnabled = push['enabled'] == true;
      provider = (push['provider'] ?? 'disabled').toString();
      mode = (model['mode'] ?? 'disabled').toString();
      localProvider = (model['local_provider'] ?? 'codex').toString();
      for (final entry in {
        'analysis_interval_minutes': 120,
        'cooldown_minutes': 240,
        'daily_limit': 2,
      }.entries) {
        field(entry.key).text = (care[entry.key] ?? entry.value).toString();
      }
      field('app_key').text = (push['app_key'] ?? '').toString();
      field('client_options').text = const JsonEncoder.withIndent('  ').convert(
        push['client_options'] ?? {'firebase': {}, 'manufacturers': {}},
      );
      for (final key in ['api_url', 'model', 'local_model', 'gateway_url']) {
        field(key).text = (model[key] ?? '').toString();
      }
      for (final key in ['master_secret', 'api_key', 'gateway_key']) {
        field(key).clear();
      }
    });
  }

  Future<void> _run(Future<void> Function() task) async {
    if (working) return;
    setState(() {
      working = true;
      error = null;
      notice = null;
    });
    try {
      await task();
    } catch (e) {
      if (mounted) error = e.toString();
    } finally {
      if (mounted) setState(() => working = false);
    }
  }

  int number(String name, int minimum, int maximum) {
    final n = int.tryParse(field(name).text);
    if (n == null || n < minimum || n > maximum) {
      throw ApiException('请输入 $minimum 到 $maximum 之间的整数。');
    }
    return n;
  }

  int get schedulerMinutes =>
      ((values?['care'] as Map?)?['scheduler_interval_minutes'] as int?) ?? 0;
  int get minimumMinutes =>
      ((values?['care'] as Map?)?['min_analysis_interval_minutes'] as int?) ??
      15;
  int analysisMinutes() {
    final n = number('analysis_interval_minutes', minimumMinutes, 1440);
    if (schedulerMinutes > 0 && n % schedulerMinutes != 0) {
      throw ApiException('分析间隔需为 $schedulerMinutes 分钟的整数倍，与服务端实际调度周期一致。');
    }
    return n;
  }

  Widget input(
    String key,
    String label, {
    bool secret = false,
    String? hint,
    int lines = 1,
  }) => Padding(
    padding: const EdgeInsets.only(bottom: 12),
    child: TextField(
      key: Key('admin-$key'),
      controller: field(key),
      enabled: !working,
      obscureText: secret,
      minLines: lines,
      maxLines: lines,
      autocorrect: !secret,
      enableSuggestions: !secret,
      decoration: InputDecoration(labelText: label, hintText: hint),
    ),
  );
  Widget section(String title, List<Widget> children) => Padding(
    padding: const EdgeInsets.only(bottom: 20),
    child: _card(
      Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(title, style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(height: 12),
          ...children,
        ],
      ),
    ),
  );
  @override
  Widget build(BuildContext context) => AnimatedBuilder(
    animation: widget.state,
    builder: (context, _) {
      if (widget.state.user['is_admin'] != true ||
          widget.state.user['is_demo'] == true) {
        return Scaffold(
          appBar: AppBar(title: const Text('服务管理')),
          body: const Center(child: Text('仅管理员可以管理服务配置。')),
        );
      }
      return Scaffold(
        appBar: AppBar(
          title: const Text('管理员服务设置'),
          actions: [
            IconButton(
              onPressed: working ? null : _load,
              icon: const Icon(Icons.refresh),
            ),
          ],
        ),
        body: ListView(
          padding: const EdgeInsets.all(18),
          children: [
            if (working) const LinearProgressIndicator(),
            if (error != null)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 10),
                child: Text(
                  error!,
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              ),
            if (notice != null)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 10),
                child: Text(notice!),
              ),
            if (values != null) ...[
              section('AI 主动关心', [
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('允许服务定期分析'),
                  value: careEnabled,
                  onChanged: working
                      ? null
                      : (v) => setState(() => careEnabled = v),
                ),
                const Text(
                  '分析不等于发消息。只有 AI 认为值得关心，且用户已授权、未处于免打扰并符合发信限制，才会生成会话。',
                ),
                const SizedBox(height: 14),
                if (schedulerMinutes > 0)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 12),
                    child: Text(
                      '当前云端每 $schedulerMinutes 分钟检查一次，分析间隔需为该周期的整数倍；实际分析时间受调度延迟影响。',
                    ),
                  ),
                input(
                  'analysis_interval_minutes',
                  '分析间隔（$minimumMinutes–1440 分钟）',
                ),
                input('cooldown_minutes', '最短发信间隔（15–10080 分钟）'),
                input('daily_limit', '每天最多主动关心（1–5 条）'),
                FilledButton(
                  onPressed: working
                      ? null
                      : () => _run(() async {
                          values!['care'] = await service.saveCare({
                            'enabled': careEnabled,
                            'analysis_interval_minutes': analysisMinutes(),
                            'cooldown_minutes': number(
                              'cooldown_minutes',
                              15,
                              10080,
                            ),
                            'daily_limit': number('daily_limit', 1, 5),
                          });
                          notice = '关心频率已保存。';
                        }),
                  child: const Text('保存关心策略'),
                ),
                TextButton(
                  onPressed: working
                      ? null
                      : () => _run(() async {
                          final r = await service.checkCare();
                          notice = (r['created'] ?? 0) == 1
                              ? '已生成关心，请查看收件箱。'
                              : '本次未生成关心。请确认授权、模型服务及当前是否适合联系。';
                          await widget.state.care.refresh();
                        }),
                  child: const Text('立即检查我的账号'),
                ),
              ]),
              section('模型服务', [
                DropdownButtonFormField<String>(
                  initialValue: mode,
                  items: const [
                    DropdownMenuItem(value: 'disabled', child: Text('暂不开启模型')),
                    DropdownMenuItem(value: 'api', child: Text('API 服务')),
                    DropdownMenuItem(
                      value: 'local',
                      child: Text('服务端 Claude / Codex'),
                    ),
                  ],
                  onChanged: working ? null : (v) => setState(() => mode = v!),
                ),
                const SizedBox(height: 14),
                if (mode == 'api') ...[
                  input('api_url', 'API 地址（HTTPS，包含 /v1）'),
                  input('model', '模型名称'),
                  input(
                    'api_key',
                    'API Key（留空保留）',
                    secret: true,
                    hint:
                        (values!['model'] as Map)['api_key_configured'] == true
                        ? '已配置，留空不会清除'
                        : '尚未配置',
                  ),
                ],
                if (mode == 'local') ...[
                  DropdownButtonFormField<String>(
                    initialValue: localProvider,
                    items: const [
                      DropdownMenuItem(value: 'codex', child: Text('Codex')),
                      DropdownMenuItem(value: 'claude', child: Text('Claude')),
                    ],
                    onChanged: working
                        ? null
                        : (v) => setState(() => localProvider = v!),
                  ),
                  const SizedBox(height: 14),
                  input('local_model', '本地模型名称（可选）'),
                  input('gateway_url', '服务端 HTTPS 网关地址'),
                  input('gateway_key', '网关令牌（留空保留）', secret: true),
                  const Text('云端服务通过受保护网关调用服务器上的 CLI，不会在手机上启动 Claude 或 Codex。'),
                ],
                FilledButton(
                  onPressed: working
                      ? null
                      : () => _run(() async {
                          values!['model'] = await service.saveModel({
                            'mode': mode,
                            'api_url': field('api_url').text,
                            'model': field('model').text,
                            'api_key': field('api_key').text,
                            'local_provider': localProvider,
                            'local_model': field('local_model').text,
                            'gateway_url': field('gateway_url').text,
                            'gateway_key': field('gateway_key').text,
                          });
                          field('api_key').clear();
                          field('gateway_key').clear();
                          notice = '模型服务已保存。';
                        }),
                  child: const Text('保存模型服务'),
                ),
              ]),
              section('Android 应用外通知 · 免费基础推送', [
                const Text(
                  '使用极光基础推送，不自动购买套餐。厂商通道仍受免费额度、厂商权限和设备设置约束。服务端 MasterSecret 不会提供给普通用户或写入 APK。',
                ),
                const SizedBox(height: 14),
                DropdownButtonFormField<String>(
                  initialValue: provider,
                  items: const [
                    DropdownMenuItem(value: 'disabled', child: Text('暂不开启推送')),
                    DropdownMenuItem(value: 'jpush', child: Text('极光免费基础推送')),
                  ],
                  onChanged: working
                      ? null
                      : (v) => setState(() => provider = v!),
                ),
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('启用应用外通知服务'),
                  value: pushEnabled,
                  onChanged: working
                      ? null
                      : (v) => setState(() => pushEnabled = v),
                ),
                input('app_key', '极光 AppKey'),
                input(
                  'master_secret',
                  'MasterSecret（留空保留）',
                  secret: true,
                  hint:
                      (values!['push'] as Map)['master_secret_configured'] ==
                          true
                      ? '已配置，无法回显'
                      : '尚未配置',
                ),
                CheckboxListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('明确清除已保存的 MasterSecret'),
                  value: clearPushSecret,
                  onChanged: working
                      ? null
                      : (v) => setState(() => clearPushSecret = v == true),
                ),
                ExpansionTile(
                  tilePadding: EdgeInsets.zero,
                  title: const Text('厂商与 FCM 客户端参数（可选）'),
                  children: [
                    const Text(
                      '这些参数用于手机 SDK，会提供给已登录客户端；不要填服务账号私钥或 MasterSecret。厂商服务还需在推送平台开通。FCM、小米、OPPO 参数可在运行时应用；华为、vivo、荣耀的应用标识需重新构建客户端。未开通的厂商离线通道不能保证送达。',
                    ),
                    const SizedBox(height: 12),
                    input('client_options', '公开 SDK 参数 JSON', lines: 12),
                  ],
                ),
                const SizedBox(height: 12),
                FilledButton(
                  onPressed: working
                      ? null
                      : () => _run(() async {
                          dynamic options;
                          try {
                            options = jsonDecode(field('client_options').text);
                          } catch (_) {
                            throw const ApiException('请输入有效的客户端参数 JSON 对象。');
                          }
                          if (options is! Map<String, dynamic>) {
                            throw const ApiException('客户端参数必须为 JSON 对象。');
                          }
                          values!['push'] = await service.savePush({
                            'provider': provider,
                            'enabled': pushEnabled,
                            'app_key': field('app_key').text,
                            'master_secret': field('master_secret').text,
                            'clear_master_secret': clearPushSecret,
                            'client_options': options,
                          });
                          field('master_secret').clear();
                          clearPushSecret = false;
                          notice = '推送设置已保存。用户仍需在自己的手机上授权通知。';
                        }),
                  child: const Text('保存推送设置'),
                ),
                TextButton(
                  onPressed: working
                      ? null
                      : () => _run(() async {
                          final r = await service.testPush();
                          notice = (r['sent'] ?? 0) > 0
                              ? '测试消息已交给推送服务；请在自己的设备确认。'
                              : '没有成功提交测试消息，请检查当前账号的设备注册与服务配置。';
                        }),
                  child: const Text('向我已注册的设备发测试通知'),
                ),
              ]),
            ],
          ],
        ),
      );
    },
  );
}
