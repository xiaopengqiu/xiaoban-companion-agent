# Windows v0.4.1 验证记录 · 2026-11-08

使用独立 `.venv-desktop`。最终构建前桌面运行时、更新器和原生窗口相关测试共79项通过，其中更新核心46项，桌面/生命周期/更新窗口33项。Node VM 实际执行更新页 JavaScript，验证异步轮询完成后“下载更新”和“重启更新”按钮可用；真实 Windows 子进程回归覆盖已退出但仍持有句柄、身份验证期间退出、存活PID身份不符。

`scripts/build-windows.ps1 -AppVersion <版本> -OutputDir desktop/dist/<版本>` 分别重建0.3.99验证样本和0.4.1正式包。版本覆盖仅写入隔离构建资源；现有 `desktop/dist/Xiaoban-Windows.exe` 没有被覆盖，也没有关闭不属于验收的进程。

- 两个冻结包的真实 HTTP 自检通过，包含近期处境、事情生命周期、记忆、偏好、聊天、删除和重启持久化；0.4.1自检前后端口49208一致。
- 两个包均在真实 WebView2 中完成登录→合成看板→设置→返回本机→独立更新窗口→关闭。主聊天窗口没有更新原生 API，打开更新窗口不导航主看板。`--self-test` 和 `--ui-smoke` 不请求公网更新服务。
- 0.4.1退出后同一临时目录重新运行 `--ui-smoke --reuse-login`，端口53852不变，`restored_login=true`。
- 两个 PyInstaller archive 中的五个网页资源已逐字节对比源码；归档更新页 HTML 常量也与修复后的源码相同。固定公钥、feed地址、内置版本均核对通过，无用户数据库或 `.credentials`。正式zip仅含 EXE、README.txt、LICENSES.txt，三者逐字节核对通过，构建生产源码的冻结指纹保持不变。
- 正式HTTPS更新端到端通过：冻结0.3.99样本从真实feed检查、验签、下载0.4.1（34,408,819 bytes）；正常启动后由可信助手完成等待退出、新版自检、保留rollback、原子替换并启动新版。助手验证真实服务与主WebView就绪确认，报告 `status=applied`、`ready_version=0.4.1`、`ready_pid=57552`。替换后EXE摘要与正式下载一致，rollback与旧版摘要一致，pending移除。停止验收自己的进程树后再次运行 `--ui-smoke --reuse-login`，原会话、60797端口、所有数据库表内容指纹、设置与profile标记均保留；验收服务全部停止。
- 真正的冻结助手故障验收通过：匹配固定公钥的合法签名，候选为从未发布的50 bytes无效 MZ 文件、版本0.4.2。报告明确为 `stage=probe`、`error_type=OSError`、`winerror=216`（Windows机器类型/可执行格式拒绝），自检目录确实创建，未生成替换备份。原EXE的SHA256、SQLite原始字节与全部表内容指纹不变；84条事件、4条记忆、1件事情、2条聊天、1个会话均保留。设置、浏览器profile、端口60875及原登录保留，失败版本不会自动重试循环。清理仅针对验收自己的 Windows Job 进程树。

证据：`dist/0.4.1/packaged-smoke.json`、`ui-smoke.json`、`ui-restart-smoke.json`；`build/bundle-verification.json`、`zip-verification.json`；`build/e2e-live-b9fa7fed4cdb42d6a0920fc9ce893736/evidence.json`、`before-ui.json`、`after-ui.json`；`build/e2e-invalid-449ab10a22074dc49ef7180b389a01b7/evidence.json`；两个端到端目录均有前后数据库指纹。构建日志为 `build-v4-fixture-fixed.log` 和 `build-v4-final-fixed.log`。提醒在合成迁移数据的基线中关闭，避免正常启动调度创建提醒干扰严格的数据指纹比较。

| 正式文件 | bytes | SHA256 |
|---|---:|---|
| `dist/0.4.1/Xiaoban-Windows.exe` | 34,408,819 | `1c4c87b37ec6a8bb6d81605342fb2b5c67f3153a5f39d222b05cdf22e7ba7351` |
| `dist/0.4.1/Xiaoban-Windows-portable.zip` | 34,030,007 | `db0f149cccc62790b3669b74614751ae36144ab91e0c50c554a2f0d10813f26e` |

便携包仍未做商业 Windows 代码签名。v0.3 没有更新器，需手动下载一次v0.4.1，之后由更新器处理新版。本次验收没有使用真实用户健康数据或模型凭证。

## v0.4.0 候选构建记录

0.4.0曾通过打包HTTP/GUI验收并发布为不可变候选，但没有通过真实升级验收：已退出且仍由父进程持有句柄的 Windows 进程无法查询映像路径，助手在等待阶段停止更新，原EXE和数据保留。首次无效MZ用例也在等待阶段停止，不能据此宣称已在自检阶段拒绝候选。随后通过真实子进程回归修复退出竞态，并在0.4.1用阶段诊断与实际自检目录重新验收。候选EXE SHA256为 `8aca7d3f827ea5e8c3f50f7901fd2b6a6797230e0a3eb0171356dc95c501e6a0`，未覆盖该不可变文件。

## v0.3 历史验证记录 · 2026-11-08

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
