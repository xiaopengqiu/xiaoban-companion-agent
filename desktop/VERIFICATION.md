# Windows v0.3 验证记录 · 2026-11-08

使用独立 `.venv-desktop`，pywebview6.2.1、PyInstaller6.22.3、cryptography50.0.2，完整环境记录在 `requirements.lock.txt`。最终包取自 Python、网页与模型运行时最后冻结后的源码，17:25 完成构建和打包后验收。

- `python -m pytest tests/test_desktop.py -q`：13 passed。
- `scripts/build-windows.ps1`：PyInstaller onefile 构建成功。
- 打包 EXE `--self-test`：`frozen=true`，health/demo/dashboard 真实 HTTP 流程通过；新增近期处境默认七天到期、过期后 `is_active=false` 且聊天排除、明确续期为长期的验证；事情创建→暂停→完成与 revision、结果和偏好保存通过。
- 同一 HTTP 自检停止服务后，用同一临时 SQLite 重新启动：端口51638不变；事情完整字段、记忆、陪伴方式/跟进/模型授权偏好、聊天记录均保留；随后事情删除、账号删除与会话失效通过。整个过程没有配置或调用真实模型。
- `index.html`、`app.js`、`lifecycle.mjs`、`companion-ui.mjs` 与 `styles.css` 均已从最终 EXE 的 PyInstaller archive 读出并逐字节对比当前源码。JavaScript 模块实际 HTTP 返回200和 JavaScript MIME，WebView2 成功执行模块后进入看板。
- 打包 EXE `--ui-smoke`：真实 WebView2 中登录页→独立合成数据看板→在线地址设置→返回本机并保留登录，通过。
- 打包 EXE 退出后以同一数据目录再次运行 `--ui-smoke --reuse-login`：同一端口51646，`restored_login=true`，通过。
- 验收结束后：0个 Xiaoban-Windows 进程，51638/51646均无监听；没有留下本机服务器。仅操作独立临时合成数据，未触碰正常用户账户。
- 证据文件：`dist/packaged-smoke.json`、`dist/ui-smoke.json`、`dist/ui-restart-smoke.json`；构建日志 `build-v3.log`。构建脚本只有全部断言通过才生成便携 zip；旧 `artifacts/` 发布文件未被覆盖。

输出：

| 文件 | bytes | SHA256 |
|---|---:|---|
| `dist/Xiaoban-Windows.exe` | 34,367,312 | `3ab3e1e3286ec75120567640ec4c9e9d2fcb81e3f2b7e31aa161a2d66f6a4ec9` |
| `dist/Xiaoban-Windows-portable.zip` | 33,988,277 | `df288759bb90d874a295d990f025e1f90cd34ceb287e04a70de2e79162da6c2d` |

便携 zip 只包含最终 EXE、README.txt、LICENSES.txt，三者内容均已逐字节核对。第三方许可已重新从本次环境收集：410,435 bytes、103份全文及元数据，含 CPython、cryptography、匹配 DLL 版本的 Microsoft WebView2 SDK license/NOTICE，以及3个安装包缺失许可文件时的上游补充。来源见 `licenses/SOURCES.md`。没有新增或假定本项目自身的 MIT 许可。

安装包不要求预装 Python，依赖已打包；使用系统 WebView2 Runtime，当前未做商业代码签名。本机模式默认使用结构化记忆和本地规则；Mem0及 Claude/Codex CLI 二进制未打入本机包。公开在线服务使用 Worker + D1、本地规则与可选模型，不运行 Mem0；自建 Python 服务可另行配置。以上离线与界面验收没有冒充云端、真实模型、健康设备或 FCM 推送的端到端验收。
