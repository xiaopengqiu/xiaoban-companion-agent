# Windows 便携版更新

从 v0.4.1 起，程序启动后在后台检查新版本，运行时每六小时检查一次。默认自动下载；下载完成不会打断当前聊天，下次启动安装，或在菜单“检查更新”里立即重启更新。该窗口也可关闭自动下载、查看下载状态和手动检查。程序退出后不继续下载；未完成的下载不会安装。

v0.3 及更早版没有更新模块，需要从 GitHub Release 下载 v0.4.1 并替换一次。升级只替换程序，不替换 `%LOCALAPPDATA%\Xiaoban`（或 `--data-dir` 指定目录）的数据库、WebView 登录资料、服务器地址和设置。可执行文件所在文件夹需要当前用户有写权限；只读目录或被安全软件锁定时，保留旧版并在更新窗口显示错误。

## 更新信任与失败处理

客户端只访问打包配置中的 HTTPS 更新源，不携带 GitHub token。更新清单使用 Ed25519 签名，公钥固定在程序中；下载还要通过版本、平台、精确字节数、SHA256 与同源 URL 校验。更新辅助程序在主程序退出后复核文件并执行新版自检，再原子替换和重启；替换或启动失败时尝试恢复保留的旧程序。异常退出和断电仍建议通过常规数据备份保护个人数据。

更新签名用于核实小伴发布来源，与 Windows Authenticode 商业代码签名不同。当前开发版仍可能显示 Windows 的未知发布者提示。

公开网站的 R2 `UPDATES` 存储发布后的安装文件。源码仓库维持私有，但更新接口和已发布的客户端文件可公开下载。应用管理员的模型配置权限不能上传可执行文件，发布者使用独立的运维凭证。

## 运维发布流程

1. 保留 `desktop/update-config.json` 中的公钥和更新源；在代码中增加版本并完成测试。默认构建输出支持版本目录，避免覆盖正在运行的旧文件：

   ```powershell
   ./scripts/build-windows.ps1 -AppVersion 0.4.1 -OutputDir artifacts/windows-v0.4.1
   ```

2. 网站 `.openai/hosting.json` 声明 `d1: "DB"` 和 `r2: "UPDATES"`。生产 Secret 保存 `DESKTOP_UPDATE_SIGNING_KEY`（PKCS8 DER 的 base64）与独立随机 `DESKTOP_UPDATE_PUBLISH_TOKEN`。更改环境配置后需部署保存的网站版本。不要修改既有 `AUTH_PEPPER`。
3. 发布命令从环境变量或忽略的 `.credentials/desktop-update-publish-token` 读取运维 token，不将其放进命令行、代码或构建产物。说明文件最多 2000 字符：

   ```powershell
   ./.venv-desktop/Scripts/python scripts/publish_desktop_update.py --exe artifacts/windows-v0.4.1/Xiaoban-Windows.exe --version 0.4.1 --notes-file docs/releases/v0.4.1-update.txt
   ```

4. 发布器计算摘要、流式上传并验证返回的签名。发布接口只接受递增版本；同版本同文件及同说明可安全重试，改动文件或说明必须增加版本。发布后保留 R2 旧对象供正在下载的客户端完成校验。
5. 发布 GitHub 对应源码和安装包，在隔离目录验证真实 HTTPS 下载、替换、重启及数据保留后再提供给用户。测试版本也必须使用实际受信任发布链路。

运维签名私钥保存在忽略的 `.credentials/desktop-update-signing-key.pk8`，请另行离线备份。不要提交、公开或打包该目录。客户端当前固定单个公钥，不能直接替换服务器私钥；将来换钥必须先用旧钥发布包含迁移逻辑的客户端。

后续客户端需兼容更新协议：保留 `--self-test --report` 并返回 `status: passed`、`frozen: true`、`server_stopped: true` 与精确的 `update_version`。正常启动需接受 `--data-dir`、`--update-ack` 和 `--update-ack-token`，在窗口与本机服务就绪后向专用更新目录写入对应的启动确认。破坏这些约定会被旧版更新助手判定为安装失败并回退。新增协议应先发布兼容迁移版本。

## 接口

- `GET /api/desktop-updates/latest`：返回 `{payload, signature}`，尚未发布时 404。
- `GET /api/desktop-updates/assets/{version}/{sha256}.exe`：不可变、公开的更新文件。
- `PUT /api/internal/desktop-updates/{version}`：独立发布 token 的二进制上传，64 MiB 上限、长度与 SHA256 校验、单发布租约及最新版本 CAS，失败不激活新清单。

发布不会改变模型配置、管理员账号或健康数据。更新检查只发送必要的版本请求，不上传聊天、记忆或健康记录。
