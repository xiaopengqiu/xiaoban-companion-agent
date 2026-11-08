# 小伴 · 智能穿戴陪伴 Agent

记住你在意的事，理解近期处境，持续跟进，并按你舒服的方式交流。运动、睡眠与饮食作为生活节奏的补充信息。

Python 服务采用 **Open Wearables 0.11.0 数据服务 + 自研陪伴扩展 + LangGraph + 可选 Mem0 OSS**。Open Wearables 的原始 MIT 源码通过脚本固定到提交 `183e8cc4f6de897dbd288c93d49a691f2cf721ae`，与陪伴业务独立部署。云端 Worker 采用独立的 JavaScript 规则实现与 D1，不运行 Python/LangGraph 或 Mem0。

源码仓库：[xiaopengqiu/xiaoban-companion-agent](https://github.com/xiaopengqiu/xiaoban-companion-agent)，当前为私有仓库，需要仓库访问权限。Web 公开注册与源码仓库可见性相互独立。

## 先运行体验

### Windows 桌面版

从 [v0.4.1 发布页](https://github.com/xiaopengqiu/xiaoban-companion-agent/releases/tag/v0.4.1) 下载 `Xiaoban-Windows-portable.zip`，解压后双击 `Xiaoban-Windows.exe`；无需预装 Python，使用系统 WebView2 Runtime。源码构建命令为 `./scripts/build-windows.ps1 -Install`，使用说明见 [desktop/README.md](desktop/README.md)。这是尚未商业代码签名的便携开发版。

v0.4.1 起默认启动后后台检查更新，运行期间每六小时检查一次；新版本自动下载并校验签名，下次启动安装，也可以从“检查更新”选择立即重启更新。本地记录、账号与设置独立保存。v0.3 及更早版需要手动换成 v0.4.1 一次，此后使用内置更新。维护者发布流程见 [docs/desktop-updates.md](docs/desktop-updates.md)。

窗口菜单“数据与账户”区分**本机数据（离线）**与**在线账户（云端）**：本机数据保存在 `%LOCALAPPDATA%\Xiaoban`，使用打包的 Python/LangGraph、本地规则与结构化记忆，不连接外部模型、Mem0、穿戴或推送服务。在线模式访问配置的 HTTPS 服务，可与 Web/手机登录同一个云端账号；两种数据域不自动上传、同步或合并。窗口关闭后，本机服务与调度任务也会停止。程序优先复用本机端口并使用持久 WebView profile，端口冲突换址时需重新登录。

### 公开注册的 Web 版

直接访问 [小伴 Web](https://xiaoban-companion.abloom-haven-3343.chatgpt.site)，可注册自己的账号或使用合成数据体验。云端代码位于 `hosted/`，使用 Worker + D1 保存独立账号与个人记录，已通过实际 HTTPS API 的注册、登录、持久化与双账号隔离验收。详细验证范围见 [docs/verification.md](docs/verification.md)。

Windows 的“在线账户”访问这个云端服务；“本机数据”使用自己的 SQLite 数据库。云端的规则回复不表示数据留在访问者电脑中。

### Python 开发运行

需要 Python 3.12+。在项目根目录执行：

```powershell
./scripts/run.ps1 -Install
```

打开 **http://127.0.0.1:8765**，选择“用模拟数据体验”，即可进入独立演示账号。演示使用明确标识的合成记录，无需模型密钥、手表或外部服务。也可以注册真实的空白账号，从手动记录开始。

Android 调试版可从 [v0.4.1 发布页](https://github.com/xiaopengqiu/xiaoban-companion-agent/releases/tag/v0.4.1) 下载 `Xiaoban-Android-debug.apk`，支持 Android 10 及以上。首次默认连接小伴云端，可与 Web 登录同一账号；已有的服务地址设置优先。APK 使用调试签名，真实手表同步和系统推送仍需真机联调。真实手机和 iOS 设置见 [mobile/README.md](mobile/README.md)。

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

- **在意的事**：记录面试、项目或个人目标，保存后续进展、暂停或完成；只有明确开启并约定时间后才生成跟进邀请。
- **慢慢了解你**：偏好、目标、重要关系、陪伴边界和近期处境。近期处境默认七天到期，可以更正、续期或明确改为长期。
- **三种陪伴方式**：听我说、一起理清、给我建议；聊天提供可编辑的记忆/事情建议，确认前不自动保存。
- **管理员服务配置**：API URL/API Key/模型或服务器Claude/Codex两种调用方式；普通用户不能修改。管理员可向其他正式账号授权或撤销管理员，最后一位管理员受到保护。
- **今日陪伴**：最近记录、日趋势、数据依据、提醒消息箱。
- **行为时间线**：记录运动、睡眠、饮水、饮食和心情；支持删除和幂等同步。
- **长期记忆**：明确偏好和目标、带证据的行为摘要；支持修改、删除和更正。明确“请记住”的对话可沉淀用户记忆。
- **持续陪伴策略**：评估数据新鲜度、用户时区、免打扰、每日预算及反馈。Python 服务运行时会定时评估；公开 Web 的定时触发器尚未开启，目前支持手动评估和配置后的签名新数据 Webhook。
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

管理员在“陪伴设置 → 模型调用配置”中配置服务，在“账号与管理员权限”中管理授权。API 与服务器 CLI 的配置、加密持久化及网关部署见 [docs/model-services.md](docs/model-services.md)。公开 Worker 的 CLI 模式需要独立服务器上的 HTTPS 网关；不会在浏览器或 Worker 内启动命令行程序。

以下 Mem0 安装方式适用于 Python 服务。Windows 离线包没有打入 Mem0；云端 Worker 也没有部署 Mem0 或 LangGraph，其结构化记忆、统计和提醒由 JavaScript 规则实现。Worker 可配置兼容 HTTPS 模型 API，仍需账号明确授权后才处理必要摘要。

`.env` 配置 `COMPANION_MODEL_BASE_URL`（含 `/v1`）、`COMPANION_MODEL_API_KEY`、`COMPANION_MODEL`。账号“提醒与设置”中的“允许模型辅助理解”默认关闭；开启后，已配置服务才可处理必要的个人摘要。配置失败会回退到明确标注的本地模式。

启用 Mem0：

```powershell
.\.venv\Scripts\python -m pip install -r requirements-memory.txt
```

按 [Mem0 OSS 配置文档](https://docs.mem0.ai/open-source/overview) 选择模型、embedding 和向量存储，将配置放进忽略的 `mem0-config.json`。设置 `MEM0_ENABLED=true`、`MEM0_CONFIG_PATH=mem0-config.json`。供应商和模型由部署者选择；外部 embedding 同样可能接收记忆文本。原始时序记录保存在结构化数据库，Mem0 只索引记忆。SQL 中当前用户的有效记忆始终是权威，索引中旧内容不会直接成为回答来源。

## 主动提醒如何运行

`python -m companion` 默认每 60 秒运行一次服务端任务，前提是 Python 进程保持运行。浏览器页面关闭后该进程可以继续工作；Windows 离线版关闭窗口则会停止内置进程与调度，不会在电脑关闭后继续提醒。每次评估先检查事件的**观测时间**，再处理行为机会和提醒限制。合成演示可以手动点击“看看有什么新发现”立即验证；真实账号的数据量不足时保持未知。

Worker 已实现需要服务端 secret 的 `POST /api/internal/evaluate`，也有平台 `scheduled` 入口及 D1 批次游标/互斥。**平台定时 trigger 尚未设置**；存在 `scheduled` 导出并不代表已经定时执行。当前不能承诺网页关闭后云端会自动周期评估，部署者需配置平台定时器或受保护的外部调用，并验证实际触发。

消息箱无需额外服务。原生手机推送需要配置服务端 FCM 服务账号，并在手机端配置同一 Firebase 项目；iOS 还需该项目的 APNs 配置。未配置时不会宣称已推送。FCM 接受发送请求也不等于设备确认展示。浏览器通知仅在页面打开时轮询接收。

本轮没有配置真实设备、厂商 OAuth 或 FCM/APNs 凭证，也没有完成真实健康数据和原生推送送达验收；这些集成仍以状态接口显示的配置情况为准。

Open Wearables 支持主动同步和可选签名 Webhook。具体接口、服务端凭证、签名和网络边界见 [docs/upstream.md](docs/upstream.md)。

## 验证

客户端发布统一包含 Windows EXE、便携 ZIP 和 Android APK。使用 [双端发布流程](docs/client-releases.md) 中的构建与 `scripts/publish_clients.py` 门禁，缺任一平台的有效安装包就停止发布。此规则也记录在 [AGENTS.md](AGENTS.md)，后续客户端版本必须执行。

```powershell
.\.venv\Scripts\python -m pytest -q
node --test tests/test_browser_lifecycle.mjs
.\.venv\Scripts\python -m compileall -q companion
node --check web/app.js
# Windows 打包与真实 WebView2/重启登录验收
./scripts/build-windows.ps1
# Worker/D1 的独立测试与构建（需先完成 Sites 项目配置）
npm --prefix hosted test
npm --prefix hosted run build
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

公开 Web 的部署产物来自 `hosted/`：在 Sites 登记 Worker 项目、将 D1 绑定为 `DB`、设置服务端 `AUTH_PEPPER` 等 secret 后，运行 `npm ci`、`npm test`、`npm run build`（工作目录 `hosted/`）。构建包含共享 `web/` 静态资源、`dist/server/index.js` 与 `hosted/drizzle/` 的数据库迁移；发布时需实际应用迁移并绑定持久数据库。账号和行为数据存 D1，不转发到开发者电脑。云端密码格式、额度、凭证变量和部署核查见 [docs/public-deployment.md](docs/public-deployment.md)。

Worker 的模型、Open Wearables、FCM 适配器仅在已配置凭证与可访问服务时启用；没有 Mem0，也不包含 Open Wearables 的 Postgres/Redis 数据栈。其定时 trigger 仍待部署配置。下面的 Docker/SQLite 说明适用于 Python 服务。

本地可使用 `docker compose up -d --build`。真实穿戴数据服务使用独立的 `deploy/compose.open-wearables.yml`。当前陪伴服务采用 SQLite、单进程调度，适合本地体验和小范围试用。对公网部署前需要 HTTPS、请求限流、备份恢复、运维监控和独立安全审查；扩展到多副本时应迁移持久化与任务队列，不能直接增加重复调度进程。

Windows/macOS Docker Desktop 内的陪伴容器连接宿主机上游时，将 `OPEN_WEARABLES_URL` 配置为 `http://host.docker.internal:8000`；容器内的 localhost 只指容器自身。手机 SDK 地址仍由 `OPEN_WEARABLES_PUBLIC_URL` 单独指定。可选 Firebase 服务账号、Mem0 配置/存储需要另外以只读凭证挂载及持久数据卷配置；默认镜像只包含核心依赖。

源码结构和数据流见 [docs/architecture.md](docs/architecture.md)，接口见 [docs/api-contract.md](docs/api-contract.md)。上游许可、版本和来源记录见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
