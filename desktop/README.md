# 小伴 Windows 版

双击 `Xiaoban-Windows.exe` 启动，不需要安装 Python。需要 Windows 10/11 与 Microsoft Edge WebView2 Runtime（现代 Windows 通常已有）。不修改注册表、不添加开机启动、不请求管理员权限。

默认是**本机数据（离线）**，仅监听随机 `127.0.0.1` 端口。窗口顶部“数据与账户”菜单可切换：

- 本机数据（离线）：完整网页功能、本地规则、结构化长期记忆、提醒消息箱，数据在 `%LOCALAPPDATA%\Xiaoban\companion.sqlite3`。不继承外部模型、Mem0、穿戴服务或推送凭证，不自动上传。窗口关闭后本机服务与提醒任务一同停止。
- 在线账户（云端）：打开配置的 HTTPS 服务，和手机使用同一云账号。云端数据由在线服务保存，与本机数据库相互独立，不自动合并。可选模型、Mem0 与推送取决于在线服务配置和用户授权。
- 在线地址 / 设备说明：修改 HTTPS 地址；设置在 `%LOCALAPPDATA%\Xiaoban\desktop-settings.json`，不保存模型密钥或账号密码。标题始终说明当前数据域。

Windows 不直接代替 HealthKit/Health Connect 采集。真实手表数据由手机 App 授权同步，Windows 在线账户可查看；本机模式可以手录饮食、饮水、运动、睡眠等。

资源解压目录和安装包不保存个人数据库。WebView 缓存位于 `%LOCALAPPDATA%\Xiaoban\webview`，启用持久 profile（非隐私模式）；日志在同目录 `desktop.log`，关闭 HTTP access log，不打印令牌。第一次随机选择本机端口，之后优先复用以保留登录来源；端口冲突才更换，并需要重新登录，本机记录仍保留。同一数据目录有 Windows 单实例锁，重复启动不会再开后台服务。配置不保存明文登录令牌。

## 构建

```powershell
./scripts/build-windows.ps1 -Install
```

可传 `-PublicAppUrl https://YOUR_SERVICE`。脚本新建独立 `.venv-desktop`、运行回归、PyInstaller onefile 打包并执行打包后的真实 HTTP 验收。输出在 `desktop/dist/`：exe、便携 zip、打包验证 JSON。

构建还会实际运行 WebView2 看板/设置页面并正常关闭，再启动第二次验证同一本机端口与登录会话恢复。测试数据使用独立临时目录。正常用户数据不参与验收。

该离线包没有打入 Mem0 依赖，其本地记忆为原有结构化记忆；大模型和 Mem0 可继续通过在线模式使用。完整 FastAPI、LangGraph、网页和 pywebview/WebView2 运行支持已打包，用户无需自行配置 Python。

## 验证

```powershell
./.venv-desktop/Scripts/python -m pytest tests/test_desktop.py -q
./.venv-desktop/Scripts/python -m desktop.main --self-test --report desktop/dist/source-smoke.json
./desktop/dist/Xiaoban-Windows.exe --self-test --report desktop/dist/packaged-smoke.json
./desktop/dist/Xiaoban-Windows.exe --ui-smoke --report desktop/dist/ui-smoke.json
```

自检只创建自己的临时演示数据，不访问正常账户数据。`--ui-smoke` 在独立临时目录实际启动 WebView2，运行登录页→模拟数据看板→设置菜单→返回本机并保留登录→正常关闭，生成报告；不使用桌面鼠标或键盘。安装包当前为未做商业代码签名的本地开发构建；在线服务可用性依实际部署状态，不伪装为已联网。
