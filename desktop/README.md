# 小伴 Windows 版 · v0.4.1

双击 `Xiaoban-Windows.exe` 启动，不需要安装 Python。需要 Windows 10/11 与 Microsoft Edge WebView2 Runtime（现代 Windows 通常已有）。不修改注册表、不添加开机启动、不请求管理员权限。通过 PowerShell 启动时使用普通 `Start-Process`，不要添加 `-WindowStyle Hidden`，否则主窗口也会隐藏。

默认是**本机数据（离线）**，仅监听随机 `127.0.0.1` 端口。窗口顶部“数据与账户”菜单可切换：

- 本机数据（离线）：完整网页功能、本地规则、结构化长期记忆、提醒消息箱，数据在 `%LOCALAPPDATA%\Xiaoban\companion.sqlite3`。不继承外部模型、Mem0、穿戴服务或推送凭证，不自动上传。窗口关闭后本机服务与提醒任务一同停止。
- 在线账户（云端）：默认打开 `https://xiaoban-companion.abloom-haven-3343.chatgpt.site`，和手机使用同一云账号。云端数据由在线服务保存，与本机数据库相互独立，不自动合并。当前公开站使用 Worker + D1、本地规则与可选模型，不运行 Python、LangGraph 或 Mem0；自建 Python 服务可另行配置 Mem0。模型与推送取决于服务端配置和用户授权。
- 在线地址 / 设备说明：修改 HTTPS 地址；设置在 `%LOCALAPPDATA%\Xiaoban\desktop-settings.json`，不保存模型密钥或账号密码。标题始终说明当前数据域。

Windows 不直接代替 HealthKit/Health Connect 采集。真实手表数据由手机 App 授权同步，Windows 在线账户可查看；本机模式可以手录饮食、饮水、运动、睡眠等。

v0.3 可以保存确认过的近期处境、关系与陪伴边界；近期处境默认七天有效，可更正、续期或明确改为长期。过期内容不再进入陪伴回复上下文。你在意的事情支持进行中、暂停、完成与结果记录，跟进默认关闭，开启时需选择时间。陪伴方式可选择先倾听、一起理清或讨论行动步骤。重要事情、记忆、偏好和聊天历史保存在当前数据域，重新打开仍保留。

管理员可在在线服务中管理模型和账号角色。模型支持兼容 API，或管理员配置的 Claude/Codex HTTPS 网关；CLI、登录状态与独立网关服务不包含在 Windows 包中，也不会在访客电脑上自动运行。个人模型处理授权默认关闭。Windows 本机模式不继承外部模型凭证，默认使用规则；联网调用需要另行配置在线服务。

资源解压目录和安装包不保存个人数据库。WebView 缓存位于 `%LOCALAPPDATA%\Xiaoban\webview`，启用持久 profile（非隐私模式）；日志在同目录 `desktop.log`，关闭 HTTP access log，不打印令牌。第一次随机选择本机端口，之后优先复用以保留登录来源；端口冲突才更换，并需要重新登录，本机记录仍保留。同一数据目录有 Windows 单实例锁，重复启动不会再开后台服务。配置不保存明文登录令牌。

## 自动更新

v0.4.1 起自带更新器。启动后在后台检查新版本，此后在应用运行期间每六小时检查一次，默认自动下载。下载完成不会关闭正在进行的聊天；下次启动时安装，也可以在“窗口 → 检查更新”中选择“重启更新”。更新窗口独立打开，关闭它可以继续聊天。取消“自动下载新版本”后，仍会检查版本，可在该窗口手动下载。

更新文件通过小伴的 HTTPS 服务分发，不需要 GitHub 登录。客户端内置发布公钥，校验更新说明的 Ed25519 签名、文件大小和 SHA256；安装前还会重新验证并在独立临时数据中运行新版自检。更新只替换便携 EXE，保留原程序备份，不替换本机 SQLite、在线地址设置或 WebView 登录缓存。网络暂时不可用时继续使用当前版本；更新失败可以在更新窗口查看状态后重试。请将便携版放在当前用户可写的文件夹。

v0.3 及更早版本没有更新器，需要**先手动下载这一次 v0.4.1 便携版**。此后新版本由更新器处理。更新签名用于确认小伴发布内容；安装包仍未做商业 Windows 代码签名。

## 构建

构建机器需要 Python 和 Node.js（用于执行更新页 JavaScript 状态回归）；便携版使用者不需要安装这些开发依赖。

```powershell
./scripts/build-windows.ps1 -Install -AppVersion 0.4.1
```

可传 `-PublicAppUrl https://YOUR_SERVICE`、`-AppVersion 0.4.1` 和 `-OutputDir desktop/dist/0.4.1`。默认读取受版本控制的 `desktop/update-config.json` 中的版本，输出在 `desktop/dist/<版本>/`：EXE、便携 zip、打包验证 JSON；zip 包含 EXE、说明和第三方许可全文 `LICENSES.txt`。打包版本覆盖仅写入隔离构建资源，不修改源码配置，也不覆盖正在运行的旧版。更新配置仅含固定公钥和服务地址，不含发布私钥、GitHub 令牌或用户账户。

HTTP 验收覆盖新网页模块、近期处境默认到期/过期排除/续期、事情增删改与状态、偏好和聊天，以及本机服务重启后的数据持久化。构建还会实际运行 WebView2 看板/设置页面、独立更新窗口并正常关闭，再启动第二次验证同一本机端口与登录会话恢复。测试数据使用独立临时目录。正常用户数据不参与验收。

该离线包没有打入 Mem0 依赖，其本地记忆为结构化记忆。完整 FastAPI、LangGraph、网页和 pywebview/WebView2 运行支持已打包，用户无需自行配置 Python。本机提醒调度需要应用进程持续运行；公开云端已提供受保护调度接口，平台定时触发器仍需单独开通，不能将已保存的跟进时间当作无人值守推送保证。

## 验证

```powershell
./.venv-desktop/Scripts/python -m pytest tests/test_desktop.py tests/test_update_ui.py tests/test_updater.py -q
./.venv-desktop/Scripts/python -m desktop.main --self-test --report desktop/dist/0.4.1/source-smoke.json
./desktop/dist/0.4.1/Xiaoban-Windows.exe --self-test --report desktop/dist/0.4.1/packaged-smoke.json
./desktop/dist/0.4.1/Xiaoban-Windows.exe --ui-smoke --report desktop/dist/0.4.1/ui-smoke.json
```

自检只创建自己的临时演示数据，不访问正常账户数据，也不会向公网检查或下载更新。`--ui-smoke` 在独立临时目录实际启动 WebView2，运行登录页→模拟数据看板→设置菜单→返回本机并保留登录→独立更新窗口→正常关闭，生成报告；不使用桌面鼠标或键盘。安装包当前为未做商业代码签名的本地开发构建；在线服务可用性依实际部署状态，不伪装为已联网。
