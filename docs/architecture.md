# 实现架构与数据约定

## 选择

以 Open Wearables 为独立数据服务，开发伴随它运行的陪伴应用。这样可以保留上游供应商适配、数据模型和迁移机制，同时独立迭代记忆和交互。没有采用 HiMe/OpenVitals 的非商用源码，也没有依赖 Omi 已退役的主动行为接口。

## 运行时与数据域

| 入口 | 账户与数据 | 实际陪伴实现 | 外部能力 |
| --- | --- | --- | --- |
| Python 服务 | 自有 SQLite 与会话 | LangGraph + 确定性策略 | 按配置和授权使用模型、Mem0 OSS、Open Wearables、FCM |
| Windows 本机数据 | `%LOCALAPPDATA%/Xiaoban` 的 SQLite、持久 WebView profile | 打包的 Python/LangGraph、本地规则与结构化记忆 | 离线模式隔离外部配置；包内不含 Mem0 |
| Windows 在线账户 / 公开 Web | 同一云端 D1 与云账号 | Worker 的 JavaScript 规则与结构化记忆 | 可选 HTTPS 模型；穿戴/FCM 适配器需另配服务和凭证；无 LangGraph、无 Mem0 |
| Flutter 手机 | 使用所配置服务的账号，token 在平台安全存储 | 通过 API 使用对应后端 | 手机执行 HealthKit / Health Connect 授权与 SDK 同步 |

本机与云端的账号、记录和会话不自动合并。桌面菜单与标题明确区分两种数据域；只有 Windows 在线模式、Web 和指向同一云端服务的手机才共享云账号。本机数据库不随切换在线模式自动上传。

源码仓库 [xiaopengqiu/xiaoban-companion-agent](https://github.com/xiaopengqiu/xiaoban-companion-agent) 当前私有。公开 Web 设计为独立注册，源码访问权限不决定 Web 登录权限；正式公网入口尚待上线验收。

以下数据流描述 Python 联网服务；Windows 离线版从手录进入结构化事实层，Worker 则用自己的 JavaScript 实现接收 API 请求与持久化。

```text
穿戴设备 / 厂商应用
    → HealthKit / Health Connect → Flutter + Open Wearables SDK
    → Open Wearables（统一模型、来源优先级、Postgres）
    → 陪伴服务的绑定用户同步 / 签名 webhook
    → SQLite 行为事实、确定性日统计
    → LangGraph 观察 / 趋势发现 / 记忆检索 / 回复
    → 有新鲜证据的候选提醒
    → 时区、免打扰、每日预算、幂等及反馈检查
    → 消息箱 / 配置后的 FCM
    → 用户更正、完成、延后或关闭
```

## 服务划分

- `companion/store.py`：有用户范围的持久化、密码和会话、去重、证据失效与删除。
- `companion/policy.py`：纯统计和时间策略。缺失数据保持未知；完整日与当日部分记录分开处理。
- `companion/agent.py`：真正执行的 LangGraph graph。趋势工作流由 observe/discover 组成；聊天工作流由 observe/respond 组成。外部语言模型是可选回复能力，不持有管理工具或凭证。
- `companion/app.py`：鉴权、数据验证、API、后台任务、连接身份绑定和通知投递。
- `companion/integrations.py`：固定 Open Wearables 版本的 HTTP、数据归一化、Svix 验签与可选 FCM。
- `companion/memory_adapter.py`：明确启用的 Mem0 OSS 索引，保留用户更正原文，强制命名空间和 SQL 权威性。
- `web/`：直接调用真实 API 的本地响应式体验，不维护一份独立的模拟业务逻辑。
- `mobile/`：共用 API 的原生双端客户端，健康平台权限和 SDK 在手机执行。
- `desktop/`：PyInstaller + pywebview/WebView2，区分本机与在线入口。资源与个人数据目录隔离，按数据目录持有单实例锁，优先复用本机端口；关闭窗口会停止内置服务。
- `hosted/worker/`：公开 Web 的 Worker API、确定性策略、可选模型和穿戴/FCM HTTP 适配器；不嵌入 Python、LangGraph 或 Mem0。
- `hosted/drizzle/`：D1 schema 与迁移。Worker 构建将共享 `web/` 内嵌为同源资源，使用 `DB` 绑定，记录保存在云端。

## 事实与记忆

每条行为保存 `kind/value/unit/start_at/end_at/source/external_id/metadata`，服务端补充内部 id 和接收/更新时间。时间必须带 UTC offset。幂等键为 `(user_id, source, external_id)`；无外部 id 的手录是独立事件。原始来源、设备、粒度和 upstream 日标签保存在 metadata。心率原始流当前不进入首版分析器。

日汇总按来源平台的 `summary_date` 对齐；它不是实时采样。睡眠采用来源报告的实际睡眠时长；仅有 session_duration 的记录不冒充实际睡眠。跨源统计选择一个来源，避免把手机和手表的镜像数据相加；这是首版简化策略，部署时仍应配置上游来源优先级。

记忆包含 category、origin、confidence、evidence_ids、created_at。来源分为用户明确表达与派生摘要。明确“记住”的聊天指令、手动记忆和修改构成用户确认的信息；普通聊天不会自动变成不可撤销的偏好。派生记忆目前是确定性统计摘要，不将多因素相关性描述成因果。

更改/删除事实会撤销依赖它的机器记忆和未发提醒。用户编辑后的记忆具有用户确认来源，更新不应被统计任务覆盖。删除的派生摘要通过抑制记录防止同一版本立即被后台重新创建。

在小伴中删除带来源编号的事件，会持久保存该用户/来源/编号的屏蔽标记，后续同步与服务重启不会把它导回。该操作仅管理小伴侧数据，不删除 Apple Health、Health Connect 或厂商原始健康记录。当前版本不提供系统健康平台与小伴之间的完整双向删除同步；源平台独立删除的记录需另在小伴中管理。

## 提醒

Python 后台服务只在进程运行期间持续执行，页面无需保持打开；Windows 离线窗口关闭后内置服务即停止。新鲜度依据行为观测时间，而不是最近上传时间。提醒先持久化，再根据配置发送。推送使用通用锁屏文案，详细健康依据在登录后的 App 查看。投递状态区分 inbox、queued、failed 等；queued 仅表示服务接受请求。

Worker 已有受服务端 secret 保护的 `POST /api/internal/evaluate` 和 `scheduled` handler，D1 中保存批次游标与调度锁。平台定时 trigger 尚未设置，所以代码导出不能作为定时已生效的证据；需要另外配置平台调度或授权外部调用并验证。受单次 D1 查询额度限制，轮询批次不能直接宣称所有账号每分钟都被评估。网页内请求和手动评估可独立触发已有策略。

首版支持基于连续睡眠记录和个人活动基线的候选机会；饮食支持记录、问答和偏好记忆。饮食未记录不代表没有吃饭，当前不会据此推断漏餐。用户可以完成、延后、跳过和关闭类别；后台再次检查过期和依据变化。

## 权限与运行范围

用户 id 来自会话，API 对每个对象强制范围检查。密码使用加盐 PBKDF2；bearer token 随机生成，仅其 hash 存入数据库。Web 持有本机浏览器 token，Flutter 使用平台安全存储。生产环境仍需完整的 HTTPS、限流和运维加固。

Open Wearables 绑定由服务端创建，客户端传入其他 user id 不改变绑定。管理 key、应用 secret、模型 key 和 Firebase 服务账号只在服务端配置。签名 webhook 只定位绑定身份并重新向可信上游读取，不直接把通知负载里的健康值当事实。

数据授权与模型处理授权分开。后者默认关闭，关闭时不把该账号的摘要送往模型/embedding。撤销已经使用过的模型处理授权会清理相应外部记忆镜像；清理状态在 API 响应中返回。账号删除先确认上游和索引清理，再删除本地账号；外部清理失败会返回明确错误并允许重试。记忆/事实更正或删除会更新对话上下文版本，旧聊天仅保留本地查看，不再作为后续模型上下文。

Python 版使用单进程 SQLite 与进程内调度，暂不提供跨实例任务互斥。Worker 版使用 D1 事务/租约与批次游标；其迁移、服务端 secret 和定时 trigger 需要独立部署。两者都不提供企业租户管理或医院级数据管线。

设备、厂商 OAuth、模型/embedding 和 FCM/APNs 凭证本轮没有配置，相关真实同步、质量与送达尚未验收。配置适配器代码并不代表服务已启用。云端部署要求和两种运行时的密码/会话边界见 [public-deployment.md](public-deployment.md)。
