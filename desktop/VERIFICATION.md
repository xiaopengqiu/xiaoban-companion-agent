# Windows 验证记录 · 2026-11-08

使用独立 `.venv-desktop`，pywebview6.2.1、PyInstaller6.22.3，完整环境记录在 `requirements.lock.txt`。

- `python -m pytest tests/test_desktop.py -q`：13 passed。
- `scripts/build-windows.ps1`：PyInstaller onefile 构建成功。
- 打包 EXE `--self-test`：`frozen=true`，资源与 health/demo/dashboard/chat/delete 的真实 HTTP 流程通过，服务正常关闭。
- 打包 EXE `--ui-smoke`：真实 WebView2 中登录页→独立合成数据看板→在线地址设置→返回本机并保留登录，通过。
- 打包 EXE 退出后以同一数据目录再次运行 `--ui-smoke --reuse-login`：同一端口58409，`restored_login=true`，通过。
- 验收结束后：0个 Xiaoban-Windows 进程，58409无监听；没有留下本机服务器。
- 额外人工观察了实际 EXE 原生窗口、完整登录页、合成数据看板与本机模式标题；没有操作正常用户账户。

输出：

| 文件 | bytes | SHA256 |
|---|---:|---|
| `dist/Xiaoban-Windows.exe` | 34,319,586 | `cef3d993d5c27530a1a927ea5320054d63f628d068990ddf169303999a5ea54b` |
| `dist/Xiaoban-Windows-portable.zip` | 33,940,312 | `4a697b466926db4f5547b8a11f47aaa68303608fd5411514af0a10e2362c947d` |

便携 zip 现包含未更改的 EXE、README.txt、LICENSES.txt。第三方许可文件为410,435 bytes，汇集103份全文及元数据，含 CPython、匹配 DLL 版本的 Microsoft WebView2 SDK license/NOTICE，以及3个安装包缺失许可文件时的上游补充。来源见 `licenses/SOURCES.md`。没有新增或假定本项目自身的 MIT 许可。

安装包不要求预装 Python，依赖已打包；使用系统 WebView2 Runtime，当前未做商业代码签名。本机模式使用结构化记忆和本地规则；Mem0未打入本机包，模型/语义记忆可通过已配置的在线服务使用。云端入口是否可用由正式部署状态决定，以上离线与界面验收没有冒充云端健康设备或推送真机验收。
