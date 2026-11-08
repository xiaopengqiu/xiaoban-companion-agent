# 小伴 · 智能穿戴陪伴 Agent

将运动、睡眠、饮食和日常对话变成有依据、可更正的用户记忆；在合适的时机提供轻提醒，并根据反馈调整陪伴。

项目采用 **Open Wearables 0.11.0 数据服务 + 自研陪伴扩展 + LangGraph + 可选 Mem0 OSS**。Open Wearables 的原始 MIT 源码通过脚本固定到提交 `183e8cc4f6de897dbd288c93d49a691f2cf721ae`，与陪伴业务独立部署；本仓库不是给一个空白聊天页面换名。

## 先运行体验

需要 Python 3.12+。在项目根目录执行：

```powershell
./scripts/run.ps1 -Install
```

打开 **http://127.0.0.1:8765**，选择“用模拟数据体验”，即可进入独立演示账号。演示使用明确标识的合成记录，无需模型密钥、手表或外部服务。也可以注册真实的空白账号，从手动记录开始。

本次已成功构建 Android 调试安装包，位于 `artifacts/companion-android-debug.apk`。安装后需在登录页配置手机可访问的陪伴服务地址；默认地址面向 Android 模拟器。真实手机和 iOS 设置见 [mobile/README.md](mobile/README.md)。

跨平台手动启动：

```bash
python -m venv .venv
# Activate the virtual environment for your shell, then:
python -m pip install -r requirements.txt
python -m companion
```

默认仅绑定本机。按 `Ctrl+C` 停止。数据保存在忽略的 `data/companion.sqlite3`；重启服务保留数据。请保护数据目录、磁盘及备份；SQLite 文件自身没有数据库加密。

`requirements.lock.txt` 记录本次验证的完整 Python 环境，包含可选 Mem0；需要精确复现该环境时可安装该文件。轻量运行使用 `requirements.txt` 即可。

## 可以做什么

- **今日陪伴**：最近记录、日趋势、数据依据、提醒消息箱。
- **行为时间线**：记录运动、睡眠、饮水、饮食和心情；支持删除和幂等同步。
- **长期记忆**：明确偏好和目标、带证据的行为摘要；支持修改、删除和更正。明确“请记住”的对话可沉淀用户记忆。
- **持续陪伴**：服务端后台评估，考虑数据新鲜度、用户时区、免打扰、每日预算及反馈；页面关闭后服务仍可运行。
- **反馈闭环**：完成、稍后、跳过、不再提醒这类。
- **有依据的对话**：本地规则模式即可使用；配置兼容模型并取得用户授权后，可使用模型组织回复。
- **双端手机源代码**：Flutter iOS/Android 客户端，健康授权、同步、手录、对话、记忆与提醒。构建和平台配置见 [mobile/README.md](mobile/README.md)。

饮食依靠用户记录及来源平台已有条目。当前版本支持文字记录，尚无食物照片识别或营养估算。健康数据同步受平台和设备约束，不能保证秒级感知；项目提供生活方式陪伴，不生成诊断。

## 接入真实手表 / 手环

1. 按 [docs/upstream.md](docs/upstream.md) 启动固定版本 Open Wearables，创建管理 API key 和应用凭证。
2. 复制 `.env.example` 为 `.env`，仅在服务端填写相关配置，然后重启陪伴服务。
3. 在 Flutter App 登录账号，连接健康数据；服务端创建并绑定 upstream 用户，手机只获得该用户范围的 SDK 令牌。
4. 在手机上授予 HealthKit / Health Connect 读取权限，开启同步，再刷新陪伴时间线。

管理员密钥、应用 secret、模型密钥和 Firebase 服务账号都不能进入 App、浏览器代码或模型上下文。真实手机需要能访问的 HTTPS 地址，`localhost` 指向手机自身。通过 `OPEN_WEARABLES_PUBLIC_URL` 可以给 SDK 返回手机可访问地址，同时让服务端保留内部 API 地址。

是否覆盖某个手环及指标，需要核实厂商 App 是否写入对应健康平台。首批验证范围建议选定 iPhone/Apple Watch 和一组实际 Android 设备。

## 模型与语义记忆（可选）

`.env` 配置 `COMPANION_MODEL_BASE_URL`（含 `/v1`）、`COMPANION_MODEL_API_KEY`、`COMPANION_MODEL`。账号“提醒与设置”中的“允许模型辅助理解”默认关闭；开启后，已配置服务才可处理必要的个人摘要。配置失败会回退到明确标注的本地模式。

启用 Mem0：

```powershell
.\.venv\Scripts\python -m pip install -r requirements-memory.txt
```

按 [Mem0 OSS 配置文档](https://docs.mem0.ai/open-source/overview) 选择模型、embedding 和向量存储，将配置放进忽略的 `mem0-config.json`。设置 `MEM0_ENABLED=true`、`MEM0_CONFIG_PATH=mem0-config.json`。供应商和模型由部署者选择；外部 embedding 同样可能接收记忆文本。原始时序记录保存在结构化数据库，Mem0 只索引记忆。SQL 中当前用户的有效记忆始终是权威，索引中旧内容不会直接成为回答来源。

## 主动提醒如何运行

`python -m companion` 默认每 60 秒运行一次服务端任务。每次评估先检查事件的**观测时间**，再处理行为机会和提醒限制。合成演示可以手动点击“看看有什么新发现”立即验证；真实账号的数据量不足时保持未知。

消息箱无需额外服务。原生手机推送需要配置服务端 FCM 服务账号，并在手机端配置同一 Firebase 项目；iOS 还需该项目的 APNs 配置。未配置时不会宣称已推送。FCM 接受发送请求也不等于设备确认展示。浏览器通知仅在页面打开时轮询接收。

Open Wearables 支持主动同步和可选签名 Webhook。具体接口、服务端凭证、签名和网络边界见 [docs/upstream.md](docs/upstream.md)。

## 验证

```powershell
.\.venv\Scripts\python -m pytest -q
node --test tests/test_browser_lifecycle.mjs
.\.venv\Scripts\python -m compileall -q companion
node --check web/app.js
```

手机端：

```bash
cd mobile
flutter pub get
flutter analyze
flutter test
```

自动测试使用独立临时数据库和网络边界 fixture，验证真实 API 行为、鉴权隔离、来源/时间、去重、更正、记忆、免打扰和提醒反馈。设备授权、后台同步、厂商准入及原生推送必须用实际设备和凭证联调。详见 [docs/verification.md](docs/verification.md)。

## 部署与范围

本地可使用 `docker compose up -d --build`。真实穿戴数据服务使用独立的 `deploy/compose.open-wearables.yml`。当前陪伴服务采用 SQLite、单进程调度，适合本地体验和小范围试用。对公网部署前需要 HTTPS、请求限流、备份恢复、运维监控和独立安全审查；扩展到多副本时应迁移持久化与任务队列，不能直接增加重复调度进程。

Windows/macOS Docker Desktop 内的陪伴容器连接宿主机上游时，将 `OPEN_WEARABLES_URL` 配置为 `http://host.docker.internal:8000`；容器内的 localhost 只指容器自身。手机 SDK 地址仍由 `OPEN_WEARABLES_PUBLIC_URL` 单独指定。可选 Firebase 服务账号、Mem0 配置/存储需要另外以只读凭证挂载及持久数据卷配置；默认镜像只包含核心依赖。

源码结构和数据流见 [docs/architecture.md](docs/architecture.md)，接口见 [docs/api-contract.md](docs/api-contract.md)。上游许可、版本和来源记录见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
