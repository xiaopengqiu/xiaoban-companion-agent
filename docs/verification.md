# 验证记录

日期：2026-11-08（Asia/Shanghai）。所有自动测试使用合成数据；没有上传用户的真实健康信息。

## 已运行

下表保留 Python/手机首版的已运行记录；Windows 与云端部署的新增范围在后文单独列出，不将它们合并为同一运行时或生产验收。

| 检查 | 结果 | 覆盖范围 |
| --- | --- | --- |
| `.venv/Scripts/python -m pytest -q` | 71 项通过 | API、策略、Open Wearables 网络边界、记忆索引和实际 Mem0 包契约 |
| `node --test tests/test_browser_lifecycle.mjs` | 3 项通过 | 跨会话旧响应隔离、首条通知、历史通知不重发 |
| `python -m compileall -q companion` | 退出 0 | Python 源码编译 |
| `node --check web/app.js` | 退出 0 | 浏览器模块语法 |
| `flutter analyze --no-pub` | No issues found | 双端客户端静态分析（Windows 使用同一目录的 ASCII 映射） |
| `flutter test --no-pub` | 11 项通过 | API 中文编码、鉴权、授权默认值、390px 页面、手录、索引失败提示 |
| Dart HTTP smoke | 通过 | 对运行中真实陪伴 API 的独立合成账号全流程 |
| Docker Compose `config --quiet` | 两份配置退出 0 | 本地服务及固定版本上游部署文件的配置解析 |
| `pip check` | No broken requirements found | 当前已安装依赖一致性 |

Mem0 2.2.1 实包验证实际运行 Memory 与本地 Qdrant 的增改删和用户过滤；仅外部 embedding 边界使用确定性 fixture，未测试真实 embedding 的语义质量。Open Wearables 测试通过 httpx.MockTransport 核对固定版本的真实路由、管理 header、SDK token body、游标分页、日期、时长及空值；这不代表实际设备已连接。

独立代码与规格审查发现并修复：跨日稍后提醒预算、发送领取超时恢复、旧聊天复活已删除记忆、记忆超过固定数量后新信息不可见、跨账号异步界面回写、新账号首条浏览器提醒漏发、时间戳及日汇总边界。

补充回归核查了餐食真实备注、饮水单位换算、心情原文、限定范围的模型事件上下文、日期紧贴中文的 30 天查询，以及用户删除事件后同步/重启不重新导入。

## 浏览器人工验证

在运行中的本地服务中，使用独立模拟账号验证：

1. 进入演示，展示 21 天合成记录和统计。
2. 保存偏好，在聊天中找回该偏好。
3. 手动保存餐食，在时间线核对备注与时间。
4. 评估主动提醒，提交完成反馈并确认状态变化。
5. 重启服务，账号、偏好和聊天仍可读取。
6. 聊天时切换页面，回复仍更新当前聊天区；退出后清理个人界面。
7. 1440px 桌面与 390px 手机布局；手机无横向溢出。
8. 浏览器控制台 error 记录为空。

截图保存在忽略的 `artifacts/companion-desktop.png` 与 `artifacts/companion-mobile-web.png`。浏览器系统通知权限未擅自开启，其逻辑由单元测试覆盖。

## 需要真实运行环境的验收

- 实际 iPhone/Apple Watch 与 Android 手环的覆盖指标、授权拒绝、后台延迟、补传及去重。
- 上游 Open Wearables Docker 服务启动、厂商 OAuth 与真实用户数据导入。本机 Docker daemon 查询未得到可用响应；本轮验证部署配置及固定源码，没有声称服务已上线。
- 用户选择的外部模型与 embedding 服务的真实调用效果、费用和供应商处理设置。
- Firebase/APNs 真机前后台送达、权限撤销与共享设备切换。FCM queued 不代表设备确认展示。
- iOS 真正的签名、编译和归档需要 macOS/Xcode；本 Windows 环境未执行。
- Android APK 构建结果在下方单独记录。

当前验证支持交付**本地可运行的开发首版**，不等于已经完成商店上架或生产环境验收。

## Android 构建

`flutter build apk --debug` 已实际成功，退出 0；保留完整 Open Wearables 和 Firebase 插件依赖。生成 `mobile/build/app/outputs/flutter-apk/app-debug.apk`，177,902,350 bytes。交付副本为 `artifacts/companion-android-debug.apk`，使用调试签名，适合本地试用。

本机已配置 Flutter3.47.9、JDK21、Android SDK、NDK28.2.13676358 与 CMake3.22.1。初次构建遇到 Windows ASCII 工程映射盘与 Pub 缓存盘符不同的 Kotlin 增量缓存问题；在 Android 工程及配置脚本中关闭该增量缓存后，真实插件编译通过。该处理没有删除健康或推送功能。

APK 编译成功不替代真机运行与健康权限/后台同步/推送验收；iOS 构建边界同上。

## Windows 便携版

`scripts/build-windows.ps1` 已使用当前冻结的 `companion/` 与 `web/` 构建 PyInstaller onefile EXE；用户不需要安装 Python，仍需系统 WebView2 Runtime。桌面回归 `tests/test_desktop.py` 共13项通过，覆盖资源路径、独立数据目录、HTTPS/loopback、离线配置隔离、端口复用/冲突回退、单实例锁和服务关闭。

直接执行打包后的 EXE，已通过：

1. `--self-test`：frozen 资源、真实本机 HTTP 的 health/demo/dashboard/chat/delete，正常停止服务。
2. `--ui-smoke`：真实 WebView2 登录页→合成数据看板→地址设置菜单→返回本机并保留登录。
3. 同一数据目录再次启动 `--ui-smoke --reuse-login`：复用同一端口，`restored_login=true`。
4. 退出后0个小伴进程，本机端口无监听；额外人工观察了实际 EXE 的登录和看板界面。

产物为 `desktop/dist/Xiaoban-Windows.exe` 与 `desktop/dist/Xiaoban-Windows-portable.zip`。详细报告、环境和摘要见 [desktop/VERIFICATION.md](../desktop/VERIFICATION.md)。安装包没有打入 Mem0；本机模式不连接外部集成。在线模式可以使用云端配置的可选能力，但这不等于已经验证在线设备数据或推送。

## 公开 Worker / D1 的状态

`hosted/` 的 Worker/D1 实现、迁移和独立测试与 Python 版本分开。云端使用 JavaScript 规则、结构化记忆和可选 HTTPS 模型，不运行 Python/LangGraph，也没有部署 Mem0。私有源码仓库为 [xiaopengqiu/xiaoban-companion-agent](https://github.com/xiaopengqiu/xiaoban-companion-agent)；公开 Web 注册不要求访问源码仓库。

公开 HTTPS 访问地址为 [小伴 Web](https://xiaoban-companion.abloom-haven-3343.chatgpt.site)。2026-11-08 Sites v1 发布成功，D1 0000–0002 迁移随部署应用；对应托管源码提交 `565a1b36435d6ee8758817459f095601a6efb525`。独立 Miniflare 测试28项通过。

在实际 HTTPS API 上，以两个临时合成账号通过：公开注册、双账号读写隔离、手录事件、聊天创建记忆、更正及删除记忆、退出撤销旧令牌、重新登录后记录仍存在、手动评估、匿名调度请求被401拒绝、未配置集成状态如实返回。两个验收账号及记录已通过 API 清理。限流及流式请求体边界由独立本地 D1/Python 测试覆盖，没有对公开服务做压力测试。

公网 API 验收使用 PowerShell HTTP 客户端。此环境 Node fetch 遇到 Cloudflare403 HTML，不能将其当作应用 JSON；内置浏览器验收接口发生连接超时，因此本轮没有完成独立的公网浏览器端到端交互验收。共享网页在实际 Windows WebView2 中已通过登录、模拟看板和会话恢复，公网使用者仍需验证各自网络和浏览器可达性。

云端已有受 secret 保护的 `POST /api/internal/evaluate`、`scheduled` 导出和 D1 调度游标；**平台定时 trigger 尚未配置**。当前没有证据支持“关闭页面后云端已自动定时陪伴”。Python 调度只在进程存活时执行，Windows 离线窗口关闭后停止。设备、外部模型、embedding、FCM/APNs 的真实凭证与实际运行验收仍未完成。

## v0.3 个人陪伴与管理员服务 · 2026-11-08

- Python最终全量138项通过；Worker/D1全量43项通过；Node页面生命周期/状态12项通过，编译、构建与差异检查通过。
- 新增近期处境到期/续期、过期历史隔离、重要事情/跟进授权/状态/结果、免打扰与预算/撤销，以及三种陪伴方式。验证包含两账号隔离、容量边界、首次确认前不写入记忆及第三人称/假设/拒绝提及时不自动建议。
- 管理员配置和角色由数据库核验；密钥AES-GCM存储、不读回；普通账号被403拒绝；角色变化立即影响旧会话；并发降级/删除保留至少一位管理员。删除最后管理员在外部清理前拒绝，最终SQL再次保护。
- 独立审查发现并修复CLI子进程遗留和HTTP慢流总时限两项P2。真实合成进程回归覆盖父先退出、超时、stdout/stderr上限、整个进程树清理；HTTP慢流超时实际取消并关闭连接。
- 实际开发机运行Codex CLI `0.166.0-alpha.5.1`，经受保护网关的真实HTTP请求返回200，中文回复校验通过。只发送合成连接测试，不使用个人记录。Claude未安装，只有参数/输出契约和失败处理测试。
- Windows最终EXE已重建，HTTP自检验证新模块JS MIME、处境默认到期/过期排除/续期、事情状态与revision、偏好/记忆/聊天重启持久化；真实WebView2和进程重启登录通过。详情见 [desktop/VERIFICATION.md](../desktop/VERIFICATION.md)。根代理另通过CUA实际看到新版首页和陪伴方式；检测到用户输入后停止进一步界面操作。
- Sites第二个托管版本发布成功，源码提交 `88d7a52607893bd82b05bd92cdad6b8a87673f62`，D1追加0003。实际HTTPS API验证近期处境/到期、未确认建议不落库、重要事情隔离、到期跟进与完成撤销、普通用户配置403、管理员授予/撤销角色及旧会话实时生效；两个临时验收账号已撤权并删除。用户指定的正式账号已注册并设为初始管理员，账号凭证未写入源码。
- 公网浏览器自动化连接仍超时，未完成完整公网浏览器交互验收。生产API/CLI网关尚未配置，实际在线聊天继续如实标为规则模式；管理员可以在页面中配置。云端自动定时触发器仍未开通，页面开启时可检查已约定的到期跟进。
