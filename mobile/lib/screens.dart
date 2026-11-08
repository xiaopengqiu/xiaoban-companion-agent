import 'dart:convert';
import 'dart:async';
import 'package:flutter/material.dart';
import 'app_state.dart';
import 'services.dart';
import 'care_screens.dart';

typedef Perform =
    Future<bool> Function(Future<void> Function() action, {String? message});

const kindNames = {
  'steps': '步数',
  'sleep': '睡眠',
  'workout': '运动',
  'meal': '饮食',
  'water': '饮水',
  'mood': '心情',
};
const categoryNames = {
  'preference': '偏好',
  'goal': '目标',
  'fact': '事实',
  'pattern': '习惯线索',
};
String text(dynamic value, [String fallback = '—']) =>
    value == null ? fallback : value.toString();
String dateLabel(dynamic value) {
  final date = DateTime.tryParse(text(value));
  if (date == null) return '暂无时间';
  final local = date.toLocal();
  String two(int number) => number.toString().padLeft(2, '0');
  return '${local.month}月${local.day}日 ${two(local.hour)}:${two(local.minute)}';
}

String eventLabel(Map<String, dynamic> event) {
  final kind = event['kind'];
  final note = (event['metadata'] as Map?)?['note'];
  final value = event['value'];
  final units = {
    'steps': '步',
    'sleep': '小时',
    'workout': '分钟',
    'water': 'mL',
    'meal': '份',
    'mood': '/ 5',
  };
  return '${kindNames[kind] ?? kind} · ${text(value)} ${units[kind] ?? text(event['unit'], '')}${note != null && note.toString().isNotEmpty ? ' · $note' : ''}';
}

IconData kindIcon(dynamic kind) => switch (kind) {
  'steps' => Icons.directions_walk_rounded,
  'sleep' => Icons.bedtime_outlined,
  'workout' => Icons.fitness_center_rounded,
  'water' => Icons.water_drop_outlined,
  'meal' => Icons.restaurant_rounded,
  'mood' => Icons.sentiment_satisfied_alt_rounded,
  _ => Icons.favorite_border_rounded,
};

Future<bool> confirm(
  BuildContext context,
  String title,
  String body, {
  String button = '删除',
}) async =>
    await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(title),
        content: Text(body),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: Text(button),
          ),
        ],
      ),
    ) ??
    false;

class CompanionShell extends StatefulWidget {
  const CompanionShell({
    super.key,
    required this.state,
    required this.health,
    required this.push,
    required this.onLogout,
  });
  final CompanionState state;
  final HealthSyncService health;
  final PushService push;
  final Future<void> Function({bool deleteAccount}) onLogout;
  @override
  State<CompanionShell> createState() => _CompanionShellState();
}

class _CompanionShellState extends State<CompanionShell>
    with WidgetsBindingObserver {
  int index = 0;
  bool working = false;
  Timer? carePoll;
  bool appActive = true;
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    carePoll = Timer.periodic(const Duration(seconds: 30), (_) {
      if (mounted && appActive) widget.state.care.refresh();
    });
    Future.microtask(() async {
      await widget.state.refresh();
      await widget.state.care.refresh();
      if (!mounted) {
        return;
      }
      await widget.push.restore(
        widget.state.api,
        refreshMessages,
        onOpenCare: openCare,
      );
      if (mounted) setState(() {});
    });
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState value) {
    appActive = value == AppLifecycleState.resumed;
    if (appActive) widget.state.care.refresh();
  }

  @override
  void dispose() {
    carePoll?.cancel();
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  void refreshMessages() {
    if (!mounted) return;
    unawaited(widget.state.refresh());
    unawaited(widget.state.care.refresh());
  }

  void openCare(String id) {
    if (!mounted) return;
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => CareConversationPage(care: widget.state.care, id: id),
      ),
    );
  }

  Future<bool> perform(
    Future<void> Function() action, {
    String? message,
  }) async {
    if (working) return false;
    setState(() => working = true);
    try {
      await action();
      if (mounted && message != null) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(message)));
      }
      return true;
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(e.toString()),
            duration: const Duration(seconds: 6),
          ),
        );
      }
      return false;
    } finally {
      if (mounted) setState(() => working = false);
    }
  }

  void inbox() {
    Navigator.push(
      context,
      MaterialPageRoute<void>(
        builder: (_) => AnimatedBuilder(
          animation: widget.state,
          builder: (_, __) => DefaultTabController(
            length: 2,
            child: Scaffold(
              appBar: AppBar(
                title: const Text('陪伴收件箱'),
                bottom: const TabBar(
                  tabs: [
                    Tab(text: '主动关心'),
                    Tab(text: '生活提醒'),
                  ],
                ),
              ),
              body: TabBarView(
                children: [
                  CareInboxPage(
                    care: widget.state.care,
                    afterPreferenceChange: widget.state.refresh,
                  ),
                  ReminderPage(state: widget.state, perform: perform),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
    animation: widget.state,
    builder: (context, _) {
      final state = widget.state;
      final pages = [
        TodayPage(state: state, perform: perform, onInbox: inbox),
        TimelinePage(state: state, perform: perform),
        MemoriesPage(state: state, perform: perform),
        ChatPage(state: state, perform: perform),
        SettingsPage(
          state: state,
          health: widget.health,
          push: widget.push,
          perform: perform,
          onLogout: widget.onLogout,
          onOpenCare: openCare,
        ),
      ];
      return Scaffold(
        appBar: AppBar(
          title: const Row(
            children: [
              Icon(Icons.spa_rounded, color: Color(0xFF196B5C)),
              SizedBox(width: 9),
              Text('小伴', style: TextStyle(fontWeight: FontWeight.w700)),
            ],
          ),
          actions: [
            IconButton(
              tooltip: '刷新',
              onPressed: working || state.loading
                  ? null
                  : () => perform(state.refresh),
              icon: const Icon(Icons.refresh_rounded),
            ),
            IconButton(
              tooltip: '陪伴收件箱',
              onPressed: inbox,
              icon: Badge(
                isLabelVisible: state.care.unreadCount > 0,
                label: Text('${state.care.unreadCount}'),
                child: const Icon(Icons.notifications_none_rounded),
              ),
            ),
            const SizedBox(width: 8),
          ],
        ),
        body: Column(
          children: [
            if (working || state.loading)
              const LinearProgressIndicator(minHeight: 2),
            if (state.error != null)
              MaterialBanner(
                content: Text(state.error!),
                actions: [
                  TextButton(
                    onPressed: () => perform(state.refresh),
                    child: const Text('重试'),
                  ),
                ],
              ),
            if (state.syncWarning != null)
              MaterialBanner(
                content: Text(state.syncWarning!),
                actions: [
                  TextButton(
                    onPressed: () => perform(
                      () => state.mutate(
                        () => state.api.patch('/preferences', {
                          'model_consent':
                              state.preferences['model_consent'] == true,
                        }),
                      ),
                    ),
                    child: const Text('重试同步'),
                  ),
                ],
              ),
            Expanded(child: pages[index]),
          ],
        ),
        bottomNavigationBar: NavigationBar(
          selectedIndex: index,
          onDestinationSelected: (value) => setState(() => index = value),
          destinations: const [
            NavigationDestination(
              icon: Icon(Icons.wb_sunny_outlined),
              selectedIcon: Icon(Icons.wb_sunny_rounded),
              label: '今日',
            ),
            NavigationDestination(
              icon: Icon(Icons.view_timeline_outlined),
              label: '时间线',
            ),
            NavigationDestination(
              icon: Icon(Icons.auto_awesome_outlined),
              label: '记忆',
            ),
            NavigationDestination(
              icon: Icon(Icons.chat_bubble_outline_rounded),
              label: '聊聊',
            ),
            NavigationDestination(
              icon: Icon(Icons.person_outline_rounded),
              label: '我的',
            ),
          ],
        ),
      );
    },
  );
}

class PageFrame extends StatelessWidget {
  const PageFrame({super.key, required this.children});
  final List<Widget> children;
  @override
  Widget build(BuildContext context) => SingleChildScrollView(
    padding: const EdgeInsets.fromLTRB(20, 12, 20, 32),
    child: Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 900),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: children,
        ),
      ),
    ),
  );
}

class Panel extends StatelessWidget {
  const Panel({super.key, required this.child, this.color});
  final Widget child;
  final Color? color;
  @override
  Widget build(BuildContext context) => Card(
    color: color,
    child: Padding(padding: const EdgeInsets.all(20), child: child),
  );
}

class SectionTitle extends StatelessWidget {
  const SectionTitle(this.title, {super.key, this.subtitle, this.action});
  final String title;
  final String? subtitle;
  final Widget? action;
  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.only(top: 20, bottom: 12),
    child: Row(
      children: [
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                title,
                style: Theme.of(
                  context,
                ).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w700),
              ),
              if (subtitle != null)
                Padding(
                  padding: const EdgeInsets.only(top: 5),
                  child: Text(
                    subtitle!,
                    style: const TextStyle(
                      fontSize: 12,
                      color: Color(0xFF6A7771),
                    ),
                  ),
                ),
            ],
          ),
        ),
        if (action != null) action!,
      ],
    ),
  );
}

class EmptyPanel extends StatelessWidget {
  const EmptyPanel(this.message, {super.key});
  final String message;
  @override
  Widget build(BuildContext context) => Panel(
    child: Text(
      message,
      style: const TextStyle(color: Color(0xFF6A7771), height: 1.7),
    ),
  );
}

class EvidenceButton extends StatelessWidget {
  const EvidenceButton({super.key, required this.ids, required this.state});
  final List<dynamic> ids;
  final CompanionState state;
  @override
  Widget build(BuildContext context) {
    if (ids.isEmpty) {
      return const SizedBox.shrink();
    }
    return TextButton.icon(
      onPressed: () => showDialog<void>(
        context: context,
        builder: (_) => AlertDialog(
          title: const Text('依据的记录'),
          content: SizedBox(
            width: 460,
            child: ListView(
              shrinkWrap: true,
              children: ids.map((id) {
                final matches = state.events.where(
                  (event) => event['id'] == id,
                );
                if (matches.isNotEmpty) {
                  return ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: Icon(kindIcon(matches.first['kind'])),
                    title: Text(eventLabel(matches.first)),
                    subtitle: Text(dateLabel(matches.first['start_at'])),
                  );
                }
                final memories = state.memories.where(
                  (memory) => memory['id'] == id,
                );
                return ListTile(
                  contentPadding: EdgeInsets.zero,
                  title: Text(
                    memories.isEmpty
                        ? '历史依据暂未加载'
                        : text(memories.first['text']),
                  ),
                  subtitle: Text('记录编号：$id'),
                );
              }).toList(),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('知道了'),
            ),
          ],
        ),
      ),
      icon: const Icon(Icons.fact_check_outlined, size: 16),
      label: Text('查看 ${ids.length} 条依据'),
    );
  }
}

class TodayPage extends StatelessWidget {
  const TodayPage({
    super.key,
    required this.state,
    required this.perform,
    required this.onInbox,
  });
  final CompanionState state;
  final Perform perform;
  final VoidCallback onInbox;
  @override
  Widget build(BuildContext context) {
    final metrics = (state.dashboard['metrics'] as Map?) ?? {};
    final insights = (state.dashboard['insights'] as List?) ?? [];
    final series = (state.dashboard['series'] as List?) ?? [];
    final demo = state.user['is_demo'] == true;
    final values = [
      ('steps', '今日步数', '步', Icons.directions_walk_rounded),
      ('sleep_hours', '今日睡眠', '小时', Icons.bedtime_outlined),
      ('workout_minutes', '今日运动', '分钟', Icons.fitness_center_rounded),
      ('water_ml', '今日饮水', 'mL', Icons.water_drop_outlined),
    ];
    return PageFrame(
      children: [
        Panel(
          color: const Color(0xFFE1EEE3),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                '${text(state.user['name'], '朋友')}，今天也一起走走',
                style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                  fontWeight: FontWeight.w700,
                ),
              ),
              const SizedBox(height: 10),
              const Text(
                '从一杯水、一次散步开始。你的每一点改变，都值得被记住。',
                style: TextStyle(height: 1.7),
              ),
              const SizedBox(height: 12),
              Wrap(
                spacing: 8,
                runSpacing: 6,
                children: [
                  Chip(
                    avatar: Icon(
                      demo
                          ? Icons.science_outlined
                          : Icons.verified_user_outlined,
                      size: 16,
                    ),
                    label: Text(demo ? '合成数据 · 独立演示' : '仅使用你已授权的数据'),
                  ),
                  Chip(
                    label: Text(
                      text(state.dashboard['mode']).contains('local')
                          ? '本地规则陪伴'
                          : '陪伴模式：${text(state.dashboard['mode'], '加载中')}',
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
        SectionTitle(
          '今天的节奏',
          subtitle:
              '统计按账号时区归集 · ${text(state.user['timezone'], 'Asia/Shanghai')}',
        ),
        LayoutBuilder(
          builder: (context, box) => Wrap(
            spacing: 10,
            runSpacing: 10,
            children: values
                .map(
                  (item) => SizedBox(
                    width:
                        (box.maxWidth - (box.maxWidth > 620 ? 30 : 10)) /
                        (box.maxWidth > 620 ? 4 : 2),
                    child: Panel(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Icon(item.$4, color: const Color(0xFF196B5C)),
                          const SizedBox(height: 12),
                          Text(
                            text(metrics[item.$1]),
                            style: Theme.of(context).textTheme.headlineMedium
                                ?.copyWith(fontWeight: FontWeight.w700),
                          ),
                          Text(
                            '${item.$2} · ${item.$3}',
                            style: const TextStyle(
                              fontSize: 12,
                              color: Color(0xFF6A7771),
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                )
                .toList(),
          ),
        ),
        const SizedBox(height: 12),
        Text(
          '最近导入：${state.dashboard['last_sync_at'] == null ? '尚无健康数据同步；可手动记录' : dateLabel(state.dashboard['last_sync_at'])}',
          style: const TextStyle(fontSize: 12, color: Color(0xFF6A7771)),
        ),
        const SectionTitle('轻轻记一笔'),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: ['water', 'meal', 'workout', 'sleep']
              .map(
                (kind) => ActionChip(
                  avatar: Icon(kindIcon(kind), size: 18),
                  label: Text(kindNames[kind]!),
                  onPressed: () =>
                      showRecordDialog(context, state, perform, kind: kind),
                ),
              )
              .toList(),
        ),
        SectionTitle(
          '小伴观察',
          subtitle: '观察来自已有记录；数据不足时会直接说明',
          action: IconButton(
            tooltip: '打开收件箱',
            onPressed: onInbox,
            icon: const Icon(Icons.arrow_forward_rounded),
          ),
        ),
        if (insights.isEmpty) const EmptyPanel('还没有足够记录形成观察。先记下今天的饮水、饮食或活动吧。'),
        ...insights.map((raw) {
          final insight = raw as Map;
          return Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: Panel(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    text(insight['title']),
                    style: const TextStyle(
                      fontSize: 16,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    text(insight['body']),
                    style: const TextStyle(height: 1.7),
                  ),
                  EvidenceButton(
                    ids: (insight['evidence_ids'] as List?) ?? [],
                    state: state,
                  ),
                ],
              ),
            ),
          );
        }),
        if (series.isNotEmpty) ...[
          const SectionTitle('最近几天', subtitle: '步数概览 · 无记录的日期不代表没有活动'),
          Panel(
            child: Column(
              children: series.map((raw) {
                final row = raw as Map;
                final steps = (row['steps'] as num?)?.toDouble();
                return Padding(
                  padding: const EdgeInsets.symmetric(vertical: 6),
                  child: Row(
                    children: [
                      SizedBox(width: 84, child: Text(text(row['date']))),
                      const SizedBox(width: 10),
                      Expanded(
                        child: LinearProgressIndicator(
                          value: steps == null
                              ? 0
                              : (steps / 10000).clamp(0, 1),
                          minHeight: 7,
                          borderRadius: BorderRadius.circular(10),
                        ),
                      ),
                      SizedBox(
                        width: 65,
                        child: Text(
                          steps == null ? '—' : '${steps.toInt()} 步',
                          textAlign: TextAlign.right,
                          style: const TextStyle(fontSize: 12),
                        ),
                      ),
                    ],
                  ),
                );
              }).toList(),
            ),
          ),
        ],
      ],
    );
  }
}

class TimelinePage extends StatelessWidget {
  const TimelinePage({super.key, required this.state, required this.perform});
  final CompanionState state;
  final Perform perform;
  @override
  Widget build(BuildContext context) {
    final items = [...state.events]
      ..sort((a, b) => text(b['start_at']).compareTo(text(a['start_at'])));
    return PageFrame(
      children: [
        SectionTitle(
          '生活时间线',
          subtitle: '原始记录和手录都在这里，删除会更新相关记忆与提醒',
          action: FilledButton.icon(
            onPressed: () => showRecordDialog(context, state, perform),
            icon: const Icon(Icons.add_rounded),
            label: const Text('记录'),
          ),
        ),
        if (items.isEmpty) const EmptyPanel('时间线还是空的。添加一条记录，或到“我的”连接手机健康数据。'),
        ...items.map(
          (event) => Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: Panel(
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  CircleAvatar(
                    backgroundColor: const Color(0xFFE7EFE6),
                    child: Icon(
                      kindIcon(event['kind']),
                      color: const Color(0xFF196B5C),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          eventLabel(event),
                          style: const TextStyle(fontWeight: FontWeight.w600),
                        ),
                        const SizedBox(height: 6),
                        Text(
                          '${dateLabel(event['start_at'])} · ${event['source'] == 'manual' ? '手动记录' : text(event['source'])}',
                          style: const TextStyle(
                            fontSize: 12,
                            color: Color(0xFF6A7771),
                          ),
                        ),
                      ],
                    ),
                  ),
                  IconButton(
                    tooltip: '删除记录',
                    icon: const Icon(Icons.delete_outline_rounded),
                    onPressed: () async {
                      final approved = await confirm(
                        context,
                        '删除这条记录？',
                        '相关的推导记忆与提醒也会重新计算。',
                      );
                      if (approved) {
                        await perform(
                          () => state.mutate(
                            () => state.api.delete(
                              '/events/${Uri.encodeComponent(text(event['id']))}',
                            ),
                          ),
                          message: '记录已删除',
                        );
                      }
                    },
                  ),
                ],
              ),
            ),
          ),
        ),
      ],
    );
  }
}

Future<void> showRecordDialog(
  BuildContext context,
  CompanionState state,
  Perform perform, {
  String kind = 'water',
}) async {
  final record = await showDialog<Map<String, dynamic>>(
    context: context,
    builder: (_) => RecordDialog(initialKind: kind),
  );
  if (record != null) {
    await perform(
      () => state.mutate(() => state.api.post('/events', record)),
      message: '记录已保存',
    );
  }
}

class RecordDialog extends StatefulWidget {
  const RecordDialog({super.key, required this.initialKind});
  final String initialKind;
  @override
  State<RecordDialog> createState() => _RecordDialogState();
}

class _RecordDialogState extends State<RecordDialog> {
  final form = GlobalKey<FormState>();
  final value = TextEditingController();
  final note = TextEditingController();
  late String kind;
  late DateTime at;
  final units = {
    'water': 'ml',
    'workout': 'min',
    'sleep': 'h',
    'steps': 'count',
    'meal': 'meal',
    'mood': 'score',
  };
  @override
  void initState() {
    super.initState();
    changeKind(widget.initialKind);
  }

  void changeKind(String next) {
    kind = next;
    value.text = switch (kind) {
      'water' => '250',
      'sleep' => '8',
      'workout' => '30',
      'meal' => '1',
      'mood' => '3',
      _ => '1000',
    };
    at = DateTime.now().subtract(
      kind == 'sleep'
          ? const Duration(hours: 8)
          : kind == 'workout'
          ? const Duration(minutes: 30)
          : Duration.zero,
    );
  }

  @override
  void dispose() {
    value.dispose();
    note.dispose();
    super.dispose();
  }

  Future<void> chooseTime() async {
    final date = await showDatePicker(
      context: context,
      initialDate: at,
      firstDate: DateTime(2000),
      lastDate: DateTime.now(),
    );
    if (date == null || !mounted) return;
    final time = await showTimePicker(
      context: context,
      initialTime: TimeOfDay.fromDateTime(at),
    );
    if (time != null && mounted) {
      setState(
        () => at = DateTime(
          date.year,
          date.month,
          date.day,
          time.hour,
          time.minute,
        ),
      );
    }
  }

  void save() {
    if (!form.currentState!.validate()) return;
    final number = double.parse(value.text);
    final duration = kind == 'sleep'
        ? Duration(minutes: (number * 60).round())
        : kind == 'workout'
        ? Duration(minutes: number.round())
        : null;
    final end = duration == null ? null : at.add(duration);
    if ((end ?? at).isAfter(DateTime.now().add(const Duration(minutes: 1)))) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('记录结束时间不能在未来，请调整开始时间或时长。')));
      return;
    }
    Navigator.pop(context, <String, dynamic>{
      'kind': kind,
      'value': number,
      'unit': units[kind],
      'start_at': at.toUtc().toIso8601String(),
      if (end != null) 'end_at': end.toUtc().toIso8601String(),
      'source': 'manual',
      'metadata': {'note': note.text.trim()},
    });
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: const Text('记录一小段日常'),
    content: SizedBox(
      width: 440,
      child: SingleChildScrollView(
        child: Form(
          key: form,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              DropdownButtonFormField<String>(
                initialValue: kind,
                decoration: const InputDecoration(labelText: '记录类型'),
                items: kindNames.entries
                    .map(
                      (e) =>
                          DropdownMenuItem(value: e.key, child: Text(e.value)),
                    )
                    .toList(),
                onChanged: (next) {
                  if (next != null) setState(() => changeKind(next));
                },
              ),
              const SizedBox(height: 14),
              TextFormField(
                controller: value,
                keyboardType: const TextInputType.numberWithOptions(
                  decimal: true,
                ),
                decoration: InputDecoration(
                  labelText: switch (kind) {
                    'water' => '饮水量（mL）',
                    'sleep' => '睡眠时长（小时）',
                    'workout' => '运动时长（分钟）',
                    'meal' => '份数（不估算热量）',
                    'mood' => '心情（1～5）',
                    _ => '步数',
                  },
                ),
                validator: (input) {
                  final n = double.tryParse(input ?? '');
                  return n == null ||
                          !n.isFinite ||
                          n <= 0 ||
                          (kind == 'mood' && (n > 5 || n != n.roundToDouble()))
                      ? '请输入有效的正数${kind == 'mood' ? '（1～5整数）' : ''}'
                      : null;
                },
              ),
              const SizedBox(height: 14),
              TextFormField(
                controller: note,
                maxLines: 3,
                decoration: InputDecoration(
                  labelText: kind == 'meal' ? '吃了什么（必填）' : '想补充的话（选填）',
                  hintText: kind == 'meal' ? '例如：午餐，米饭、青菜和鸡肉' : null,
                ),
                validator: (input) =>
                    kind == 'meal' && (input == null || input.trim().isEmpty)
                    ? '请描述这餐吃了什么'
                    : null,
              ),
              const SizedBox(height: 10),
              ListTile(
                contentPadding: EdgeInsets.zero,
                title: Text(
                  kind == 'sleep' || kind == 'workout'
                      ? '开始时间（手机本地时区）'
                      : '记录时间（手机本地时区）',
                ),
                subtitle: Text(dateLabel(at.toIso8601String())),
                trailing: const Icon(Icons.edit_calendar_outlined),
                onTap: chooseTime,
              ),
            ],
          ),
        ),
      ),
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.pop(context),
        child: const Text('取消'),
      ),
      FilledButton(onPressed: save, child: const Text('保存')),
    ],
  );
}

class MemoriesPage extends StatelessWidget {
  const MemoriesPage({super.key, required this.state, required this.perform});
  final CompanionState state;
  final Perform perform;
  Future<void> edit(
    BuildContext context, [
    Map<String, dynamic>? memory,
  ]) async {
    final result = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (_) => MemoryDialog(memory: memory),
    );
    if (result == null) return;
    await perform(
      () => state.mutate(
        () => memory == null
            ? state.api.post('/memories', result)
            : state.api.patch(
                '/memories/${Uri.encodeComponent(text(memory['id']))}',
                {'text': result['text']},
              ),
      ),
      message: '记忆已保存，以你的更正为准',
    );
  }

  @override
  Widget build(BuildContext context) => PageFrame(
    children: [
      SectionTitle(
        '慢慢了解你',
        subtitle: '你可以查看、更正和删除任何记忆',
        action: FilledButton.icon(
          onPressed: () => edit(context),
          icon: const Icon(Icons.add_rounded),
          label: const Text('告诉小伴'),
        ),
      ),
      if (state.memories.isEmpty)
        const EmptyPanel('还没有记忆。你可以告诉小伴自己的偏好与目标，例如“希望周末多走走”。'),
      ...state.memories.map(
        (memory) => Padding(
          padding: const EdgeInsets.only(bottom: 12),
          child: Panel(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Chip(
                      label: Text(categoryNames[memory['category']] ?? '记忆'),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        memory['origin'] == 'user' ||
                                memory['origin'] == 'manual'
                            ? '你告诉小伴的'
                            : '从记录中得到的线索',
                        style: const TextStyle(
                          fontSize: 12,
                          color: Color(0xFF6A7771),
                        ),
                      ),
                    ),
                    PopupMenuButton<String>(
                      onSelected: (action) async {
                        if (action == 'edit') {
                          await edit(context, memory);
                          return;
                        }
                        if (await confirm(
                          context,
                          '忘记这条记忆？',
                          '删除后，小伴不会再用这条记忆陪伴你。',
                        )) {
                          await perform(
                            () => state.mutate(
                              () => state.api.delete(
                                '/memories/${Uri.encodeComponent(text(memory['id']))}',
                              ),
                            ),
                            message: '这条记忆已删除',
                          );
                        }
                      },
                      itemBuilder: (_) => const [
                        PopupMenuItem(value: 'edit', child: Text('更正')),
                        PopupMenuItem(value: 'delete', child: Text('删除')),
                      ],
                    ),
                  ],
                ),
                const SizedBox(height: 6),
                Text(
                  text(memory['text']),
                  style: const TextStyle(fontSize: 16, height: 1.7),
                ),
                if (memory['confidence'] is num)
                  Padding(
                    padding: const EdgeInsets.only(top: 7),
                    child: Text(
                      '当前置信度 ${((memory['confidence'] as num) * 100).round()}% · ${dateLabel(memory['created_at'])}',
                      style: const TextStyle(
                        fontSize: 11,
                        color: Color(0xFF6A7771),
                      ),
                    ),
                  ),
                EvidenceButton(
                  ids: (memory['evidence_ids'] as List?) ?? [],
                  state: state,
                ),
              ],
            ),
          ),
        ),
      ),
    ],
  );
}

class MemoryDialog extends StatefulWidget {
  const MemoryDialog({super.key, this.memory});
  final Map<String, dynamic>? memory;
  @override
  State<MemoryDialog> createState() => _MemoryDialogState();
}

class _MemoryDialogState extends State<MemoryDialog> {
  late final controller = TextEditingController(text: widget.memory?['text']);
  late String category = text(widget.memory?['category'], 'preference');
  final form = GlobalKey<FormState>();
  @override
  void dispose() {
    controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: Text(widget.memory == null ? '想让小伴记住什么？' : '以你的更正为准'),
    content: SizedBox(
      width: 440,
      child: Form(
        key: form,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (widget.memory == null) ...[
              DropdownButtonFormField<String>(
                initialValue: category,
                decoration: const InputDecoration(labelText: '记忆类型'),
                items: categoryNames.entries
                    .map(
                      (e) =>
                          DropdownMenuItem(value: e.key, child: Text(e.value)),
                    )
                    .toList(),
                onChanged: (v) => setState(() => category = v!),
              ),
              const SizedBox(height: 14),
            ],
            TextFormField(
              controller: controller,
              maxLines: 5,
              maxLength: 1000,
              decoration: const InputDecoration(
                labelText: '记忆内容',
                hintText: '例如：我不喜欢早晨收到提醒',
              ),
              validator: (v) =>
                  v == null || v.trim().isEmpty ? '写一点内容再保存吧' : null,
            ),
          ],
        ),
      ),
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.pop(context),
        child: const Text('取消'),
      ),
      FilledButton(
        onPressed: () {
          if (form.currentState!.validate()) {
            Navigator.pop(context, {
              'text': controller.text.trim(),
              'category': category,
            });
          }
        },
        child: const Text('记住了'),
      ),
    ],
  );
}

class ChatPage extends StatefulWidget {
  const ChatPage({super.key, required this.state, required this.perform});
  final CompanionState state;
  final Perform perform;
  @override
  State<ChatPage> createState() => _ChatPageState();
}

class _ChatPageState extends State<ChatPage> {
  final input = TextEditingController();
  bool sending = false;
  List<dynamic> evidence = [];
  @override
  void dispose() {
    input.dispose();
    super.dispose();
  }

  Future<void> send([String? suggestion]) async {
    final message = (suggestion ?? input.text).trim();
    if (message.isEmpty || sending) return;
    setState(() => sending = true);
    try {
      final success = await widget.perform(() async {
        final result = await widget.state.api.post('/chat', {
          'message': message,
        });
        widget.state.inspectResult(result);
        evidence = (result['evidence'] as List?) ?? [];
        await widget.state.refresh();
      });
      if (success) input.clear();
    } finally {
      if (mounted) setState(() => sending = false);
    }
  }

  @override
  Widget build(BuildContext context) => Center(
    child: ConstrainedBox(
      constraints: const BoxConstraints(maxWidth: 900),
      child: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 8, 20, 12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '聊聊你的一天',
                  style: Theme.of(
                    context,
                  ).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w700),
                ),
                const SizedBox(height: 5),
                Text(
                  text(widget.state.dashboard['mode']).contains('local')
                      ? '当前为本地规则回复 · 已接入记录与记忆，不代表大模型'
                      : '结合你的已授权记录与记忆回复',
                  style: const TextStyle(
                    fontSize: 12,
                    color: Color(0xFF6A7771),
                  ),
                ),
              ],
            ),
          ),
          Expanded(
            child: widget.state.messages.isEmpty
                ? const Center(
                    child: Padding(
                      padding: EdgeInsets.all(24),
                      child: Text(
                        '你可以问：“我最近的生活节奏怎么样？”\n也可以从一件今天的小事开始聊。',
                        textAlign: TextAlign.center,
                        style: TextStyle(height: 1.8),
                      ),
                    ),
                  )
                : ListView.builder(
                    reverse: true,
                    padding: const EdgeInsets.all(20),
                    itemCount: widget.state.messages.length,
                    itemBuilder: (context, index) {
                      final message = widget
                          .state
                          .messages[widget.state.messages.length - 1 - index];
                      final own = message['role'] == 'user';
                      return Align(
                        alignment: own
                            ? Alignment.centerRight
                            : Alignment.centerLeft,
                        child: ConstrainedBox(
                          constraints: const BoxConstraints(maxWidth: 640),
                          child: Container(
                            margin: const EdgeInsets.only(bottom: 14),
                            padding: const EdgeInsets.all(16),
                            decoration: BoxDecoration(
                              color: own
                                  ? const Color(0xFF196B5C)
                                  : Colors.white,
                              borderRadius: BorderRadius.circular(20),
                            ),
                            child: SelectableText(
                              text(message['content']),
                              style: TextStyle(
                                color: own
                                    ? Colors.white
                                    : const Color(0xFF263F34),
                                height: 1.8,
                              ),
                            ),
                          ),
                        ),
                      );
                    },
                  ),
          ),
          if (evidence.isNotEmpty)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 20),
              child: Text(
                '本次依据：${evidence.map((e) => e is Map ? text(e['label']) : text(e)).join('、')}',
                style: const TextStyle(fontSize: 11, color: Color(0xFF6A7771)),
              ),
            ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
            child: Wrap(
              spacing: 7,
              children: ['今天的状态怎么样？', '你记住了我什么？']
                  .map(
                    (q) => ActionChip(
                      label: Text(q, style: const TextStyle(fontSize: 12)),
                      onPressed: sending ? null : () => send(q),
                    ),
                  )
                  .toList(),
            ),
          ),
          SafeArea(
            top: false,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Expanded(
                    child: TextField(
                      controller: input,
                      minLines: 1,
                      maxLines: 5,
                      maxLength: 2000,
                      decoration: const InputDecoration(
                        hintText: '说点什么…',
                        counterText: '',
                      ),
                      onSubmitted: (_) => send(),
                    ),
                  ),
                  const SizedBox(width: 10),
                  IconButton.filled(
                    tooltip: '发送',
                    onPressed: sending ? null : send,
                    icon: sending
                        ? const SizedBox(
                            height: 20,
                            width: 20,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Icon(Icons.arrow_upward_rounded),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    ),
  );
}

class ReminderPage extends StatelessWidget {
  const ReminderPage({super.key, required this.state, required this.perform});
  final CompanionState state;
  final Perform perform;
  @override
  Widget build(BuildContext context) => PageFrame(
    children: [
      const SectionTitle('在合适的时候，陪你一下', subtitle: '尊重免打扰时间、每天提醒额度和你的反馈'),
      OutlinedButton.icon(
        onPressed: () => perform(
          () => state.mutate(() => state.api.post('/evaluate', {})),
          message: '已评估最新记录；符合条件的提醒会出现在这里',
        ),
        icon: const Icon(Icons.auto_awesome_outlined),
        label: const Text('看看有没有值得提醒的事'),
      ),
      const SizedBox(height: 16),
      if (state.reminders.isEmpty)
        const EmptyPanel('现在没有需要打扰你的事。记录足够且时机合适时，小伴会在这里留一条提醒。'),
      ...state.reminders.map((item) {
        final status = text(item['status']);
        final closed = [
          'done',
          'dismissed',
          'disabled',
          'expired',
          'cancelled',
        ].contains(status);
        final delivery = text(item['delivery_status'], 'inbox');
        return Padding(
          padding: const EdgeInsets.only(bottom: 14),
          child: Panel(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Icon(
                      kindIcon(item['category']),
                      color: const Color(0xFF196B5C),
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(
                        text(item['title']),
                        style: const TextStyle(
                          fontSize: 17,
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 12),
                Text(text(item['body']), style: const TextStyle(height: 1.8)),
                const SizedBox(height: 8),
                Text(
                  '${{'pending': '等待反馈', 'snoozed': '已稍后提醒', 'done': '已完成', 'dismissed': '已忽略', 'disabled': '该类提醒已关闭', 'expired': '已过期', 'cancelled': '已取消'}[status] ?? status} · ${{'inbox': '应用收件箱', 'not_configured': '推送未配置', 'pending': '等待投递', 'sent': '推送已发送', 'failed': '推送失败'}[delivery] ?? delivery}',
                  style: const TextStyle(
                    fontSize: 12,
                    color: Color(0xFF6A7771),
                  ),
                ),
                if (item['scheduled_at'] != null)
                  Text(
                    '计划时间：${dateLabel(item['scheduled_at'])}',
                    style: const TextStyle(
                      fontSize: 12,
                      color: Color(0xFF6A7771),
                    ),
                  ),
                EvidenceButton(
                  ids: (item['evidence_ids'] as List?) ?? [],
                  state: state,
                ),
                if (!closed)
                  Wrap(
                    spacing: 5,
                    runSpacing: 5,
                    children:
                        [
                              ('done', '做到了'),
                              ('snooze', '30 分钟后'),
                              ('dismiss', '这次忽略'),
                              ('disable', '关闭该类'),
                            ]
                            .map(
                              (entry) => TextButton(
                                onPressed: () => perform(
                                  () => state.mutate(
                                    () => state.api.post(
                                      '/reminders/${Uri.encodeComponent(text(item['id']))}/feedback',
                                      {
                                        'action': entry.$1,
                                        if (entry.$1 == 'snooze') 'minutes': 30,
                                      },
                                    ),
                                  ),
                                  message: '已记录你的选择',
                                ),
                                child: Text(entry.$2),
                              ),
                            )
                            .toList(),
                  ),
              ],
            ),
          ),
        );
      }),
    ],
  );
}

class SettingsPage extends StatelessWidget {
  const SettingsPage({
    super.key,
    required this.state,
    required this.health,
    required this.push,
    required this.perform,
    required this.onLogout,
    this.onOpenCare,
  });
  final CompanionState state;
  final HealthSyncService health;
  final PushService push;
  final Perform perform;
  final Future<void> Function({bool deleteAccount}) onLogout;
  final ValueChanged<String>? onOpenCare;
  @override
  Widget build(BuildContext context) {
    final wearable = (state.integrations['open_wearables'] as Map?) ?? {};
    return PageFrame(
      children: [
        SectionTitle(
          text(state.user['name'], '我的小伴'),
          subtitle:
              '${text(state.user['email'], '演示账号')} · ${state.user['is_demo'] == true ? '合成数据演示' : '个人账号'}',
        ),
        if (state.user['is_admin'] == true && state.user['is_demo'] != true)
          Padding(
            padding: const EdgeInsets.only(bottom: 14),
            child: OutlinedButton.icon(
              onPressed: () => Navigator.push(
                context,
                MaterialPageRoute<void>(
                  builder: (_) => CareAdminPage(state: state),
                ),
              ),
              icon: const Icon(Icons.admin_panel_settings_outlined),
              label: const Text('管理关心频率、模型与通知服务'),
            ),
          ),
        const SectionTitle('连接真实的日常'),
        Panel(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text(
                '手机健康数据',
                style: TextStyle(fontSize: 17, fontWeight: FontWeight.w700),
              ),
              const SizedBox(height: 8),
              Text(
                wearable['configured'] == true
                    ? (wearable['connected'] == true
                          ? '服务已绑定健康账号；手机授权与实际同步请以下方状态为准。'
                          : '服务已配置，可以连接手机健康数据。')
                    : '服务端未配置 Open Wearables；仍可手录和体验完整陪伴流程。',
                style: const TextStyle(height: 1.7),
              ),
              const SizedBox(height: 8),
              Text(
                health.status,
                style: const TextStyle(fontSize: 12, color: Color(0xFF6A7771)),
              ),
              const SizedBox(height: 12),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  FilledButton.icon(
                    onPressed: () => perform(() async {
                      final message = await health.connect(state.api);
                      await state.refresh();
                      if (context.mounted) {
                        ScaffoldMessenger.of(
                          context,
                        ).showSnackBar(SnackBar(content: Text(message)));
                      }
                    }),
                    icon: const Icon(Icons.watch_outlined),
                    label: const Text('授权并连接'),
                  ),
                  OutlinedButton(
                    onPressed: () => perform(() async {
                      final result = await state.api.post(
                        '/integrations/open-wearables/sync',
                        {},
                      );
                      state.inspectResult(result);
                      await state.refresh();
                      if (context.mounted) {
                        ScaffoldMessenger.of(context).showSnackBar(
                          SnackBar(
                            content: Text(
                              '已导入 ${text(result['imported'], '0')} 条记录。${text(result['detail'], '')}',
                            ),
                          ),
                        );
                      }
                    }),
                    child: const Text('从服务导入'),
                  ),
                  TextButton(
                    onPressed: () =>
                        perform(health.disconnect, message: '手机后台健康同步已停止'),
                    child: const Text('断开手机同步'),
                  ),
                ],
              ),
              const SizedBox(height: 8),
              const Text(
                '连接只请求步数、睡眠、运动、饮水和心率。手机先同步到 Open Wearables，再导入陪伴服务；首次同步可能需要等待。',
                style: TextStyle(
                  fontSize: 12,
                  height: 1.7,
                  color: Color(0xFF6A7771),
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 14),
        Panel(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text(
                '系统通知',
                style: TextStyle(fontSize: 17, fontWeight: FontWeight.w700),
              ),
              const SizedBox(height: 10),
              Text(push.status, style: const TextStyle(height: 1.7)),
              const SizedBox(height: 8),
              const Text('允许后会向已配置的通知服务注册本机推送标识，用于小伴主动关心。应用内收件箱无需开启系统通知。'),
              const SizedBox(height: 12),
              OutlinedButton.icon(
                onPressed: () => perform(() async {
                  final message = await push.enable(state.api, () {
                    state.refresh();
                    state.care.refresh();
                  }, onOpenCare: onOpenCare);
                  if (context.mounted) {
                    ScaffoldMessenger.of(
                      context,
                    ).showSnackBar(SnackBar(content: Text(message)));
                  }
                }),
                icon: const Icon(Icons.notifications_active_outlined),
                label: const Text('开启系统推送'),
              ),
              TextButton(
                onPressed: () => perform(push.disconnect, message: '本机系统推送已关闭'),
                child: const Text('关闭本机系统推送'),
              ),
            ],
          ),
        ),
        const SectionTitle('按你的节奏提醒'),
        if (state.preferences.isEmpty)
          const EmptyPanel('提醒偏好加载中…')
        else
          PreferencesForm(
            key: ValueKey(jsonEncode(state.preferences)),
            state: state,
            perform: perform,
          ),
        const SectionTitle('你的数据，由你决定'),
        Panel(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text(
                '登录会话仅保存在系统安全存储中。可在时间线和记忆页逐条更正、删除；删除账号会清除陪伴服务中的个人数据。',
                style: TextStyle(height: 1.7),
              ),
              const SizedBox(height: 8),
              SelectableText(
                '当前服务：${state.api.baseUrl}',
                style: const TextStyle(fontSize: 12, color: Color(0xFF6A7771)),
              ),
              const SizedBox(height: 12),
              Wrap(
                spacing: 8,
                children: [
                  OutlinedButton(
                    onPressed: () =>
                        perform(() => onLogout(deleteAccount: false)),
                    child: const Text('退出登录 / 切换服务'),
                  ),
                  TextButton(
                    onPressed: () async {
                      final approved = await confirm(
                        context,
                        '删除账号及全部个人数据？',
                        '这会停止本机健康同步，并删除陪伴服务里的记录、记忆、对话、提醒与会话，以及已绑定的 Open Wearables 用户档案。系统健康数据仍保留在手机健康 App 中。此操作无法撤销。',
                        button: '确认删除',
                      );
                      if (approved) {
                        await perform(() => onLogout(deleteAccount: true));
                      }
                    },
                    child: Text(
                      '删除账号',
                      style: TextStyle(
                        color: Theme.of(context).colorScheme.error,
                      ),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class PreferencesForm extends StatefulWidget {
  const PreferencesForm({
    super.key,
    required this.state,
    required this.perform,
  });
  final CompanionState state;
  final Perform perform;
  @override
  State<PreferencesForm> createState() => _PreferencesFormState();
}

class _PreferencesFormState extends State<PreferencesForm> {
  late bool enabled = widget.state.preferences['enabled'] != false;
  late bool modelConsent = widget.state.preferences['model_consent'] == true;
  late final start = TextEditingController(
    text: text(widget.state.preferences['quiet_start'], '22:00'),
  );
  late final end = TextEditingController(
    text: text(widget.state.preferences['quiet_end'], '08:00'),
  );
  late final timezone = TextEditingController(
    text: text(widget.state.preferences['timezone'], 'Asia/Shanghai'),
  );
  late final limit = TextEditingController(
    text: text(widget.state.preferences['daily_limit'], '2'),
  );
  late final categories = Set<String>.from(
    (widget.state.preferences['reminder_categories'] as List?) ??
        ['sleep', 'activity', 'meal'],
  );
  final form = GlobalKey<FormState>();
  @override
  void dispose() {
    for (final c in [start, end, timezone, limit]) {
      c.dispose();
    }
    super.dispose();
  }

  String? timeValidator(String? value) =>
      RegExp(r'^([01]\d|2[0-3]):[0-5]\d$').hasMatch(value ?? '')
      ? null
      : '使用 HH:MM 格式';
  @override
  Widget build(BuildContext context) => Panel(
    child: Form(
      key: form,
      child: Column(
        children: [
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            title: const Text('允许主动陪伴'),
            subtitle: const Text('关闭后停止生成主动提醒'),
            value: enabled,
            onChanged: (v) => setState(() => enabled = v),
          ),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            title: const Text('允许外部模型处理必要摘要'),
            subtitle: const Text(
              '默认关闭。开启后，服务端已配置的大模型与 Mem0 可处理必要的个人摘要；关闭时使用本地规则。',
            ),
            value: modelConsent,
            onChanged: (v) => setState(() => modelConsent = v),
          ),
          const SizedBox(height: 8),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: TextFormField(
                  controller: start,
                  decoration: const InputDecoration(labelText: '免打扰开始'),
                  validator: timeValidator,
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: TextFormField(
                  controller: end,
                  decoration: const InputDecoration(labelText: '免打扰结束'),
                  validator: timeValidator,
                ),
              ),
            ],
          ),
          const SizedBox(height: 14),
          TextFormField(
            controller: timezone,
            decoration: const InputDecoration(labelText: '提醒时区（IANA）'),
            validator: (v) => v == null || v.trim().isEmpty ? '请输入时区' : null,
          ),
          const SizedBox(height: 14),
          TextFormField(
            controller: limit,
            keyboardType: TextInputType.number,
            decoration: const InputDecoration(labelText: '每天最多几条提醒'),
            validator: (v) {
              final n = int.tryParse(v ?? '');
              return n == null || n < 0 || n > 10 ? '请输入 0～10 的整数' : null;
            },
          ),
          const SizedBox(height: 14),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [('sleep', '睡眠'), ('activity', '活动'), ('meal', '饮食')]
                .map(
                  (entry) => FilterChip(
                    label: Text(entry.$2),
                    selected: categories.contains(entry.$1),
                    onSelected: (selected) => setState(() {
                      if (selected) {
                        categories.add(entry.$1);
                      } else {
                        categories.remove(entry.$1);
                      }
                    }),
                  ),
                )
                .toList(),
          ),
          const SizedBox(height: 16),
          SizedBox(
            width: double.infinity,
            child: FilledButton(
              onPressed: () {
                if (form.currentState!.validate()) {
                  widget.perform(
                    () => widget.state.mutate(
                      () => widget.state.api.patch('/preferences', {
                        'enabled': enabled,
                        'model_consent': modelConsent,
                        'quiet_start': start.text,
                        'quiet_end': end.text,
                        'timezone': timezone.text.trim(),
                        'daily_limit': int.parse(limit.text),
                        'reminder_categories': categories.toList(),
                      }),
                    ),
                    message: '已按你的节奏调整提醒',
                  );
                }
              },
              child: const Text('保存提醒偏好'),
            ),
          ),
        ],
      ),
    ),
  );
}
