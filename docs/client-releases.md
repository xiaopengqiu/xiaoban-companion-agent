# Windows 与 Android 联合发布

用户要求后续客户端版本同时提供 Windows 和 Android 安装包。统一入口为 `scripts/publish_clients.py`；缺少任一平台、验证报告、正确版本或签名即停止。Android 当前提供调试签名 APK，不等于应用商店正式版，也尚无应用内自动更新。

## 构建

使用本仓库 `.venv-desktop` 与已安装的 Flutter、Android SDK、JDK。新版本先提交生产代码，保留实际源码提交与构建输入指纹。以下以未来 `0.4.2`、Android `versionCode=3` 为例；应使用比上一次 Android 构建更大的数字，并将 `PreviousApk` 指向上一次发布的 APK：

```powershell
./scripts/build-windows.ps1 -AppVersion 0.4.2
./scripts/build-android.ps1 -AppVersion 0.4.2 -BuildNumber 3 -PreviousApk artifacts/android/0.4.1/Xiaoban-Android-debug.apk
```

Windows 输出在 `desktop/dist/<版本>/`，含 EXE、portable ZIP 和三份真实自检报告。Android 输出在 `artifacts/android/<版本>/`，含 `Xiaoban-Android-debug.apk` 与 `build-report.json`。构建脚本实际执行 Flutter 分析、测试、APK 构建、SDK 包信息与签名检查，验证默认小伴云端 URL、版本号、versionCode 递增及调试签名身份连续性。

调试 keystore 不进入仓库。换电脑构建前应安全迁移同一签名身份；换成另一个调试证书的 APK 不能覆盖旧安装。将来切换正式签名需单独设计迁移与分发方案。

## 预检与上传

```powershell
./.venv-desktop/Scripts/python -X utf8 scripts/publish_clients.py --version 0.4.2 --dry-run
```

`--dry-run` 执行所有本地检查，包含真实 PE 版本解析、`aapt` / `apksigner` 只读检查、报告摘要和当前生产源码比对，生成可查看的客户端校验清单；不联系 GitHub。使用安装了 PyInstaller/pefile 的桌面构建环境，以及 Android SDK/JDK。它不代替实际上传前对目标 Git tag 和现有远端附件的校验。

新版本先创建 draft Release，使用准确的完整源码提交、对应版本的发布说明文件。示例中的 `COMMIT_SHA` 必须替换为实际完整提交，不能使用未提交生产代码的工作区：

```powershell
gh release create v0.4.2 --draft --target COMMIT_SHA --title '小伴 v0.4.2' --notes-file docs/releases/v0.4.2.md
./.venv-desktop/Scripts/python -X utf8 scripts/publish_clients.py --version 0.4.2
gh release edit v0.4.2 --draft=false
```

统一门禁在任何上传前读取目标 tag 对应提交，并对 Android 完整生产文件清单逐项校验：构建时原始字节必须保持，Git clean 过滤后的 blob 必须与 tag 相同，因此支持 Windows CRLF 行尾，也拒绝漏文件或新代码冒充旧版本。发布机需有该提交的本地 Git 对象；必要时先 fetch。文档和运维脚本可以在同一生产源码之外追加，不需要改动已发布 tag。

上传的四个附件为：

- `Xiaoban-Windows.exe`
- `Xiaoban-Windows-portable.zip`
- `Xiaoban-Android-debug.apk`
- `SHA256SUMS-clients-v<版本>.txt`

所有同名远端附件在上传前逐一核对 SHA256、大小与完成状态。相同文件安全跳过；不同文件立即停止，绝不使用 `--clobber`。上传后再次核对全部远端摘要。GitHub 附件上传不是多文件事务，网络中断时先保持 draft，按相同命令重试续传，完整通过后再公开。

源码仍应提交并推送到 GitHub。若附加源码 ZIP，必须从对应 tag 的完整源码提交创建，并单独核对摘要；本入口不替换已发布的源码 ZIP 或旧校验清单。

## 补齐已有版本

已有公开 Release 缺 Android 时，也使用同一命令。只有生产输入与其原 tag 一致的 APK 可以补齐；编译参数可覆盖版本名和 versionCode，不把新功能伪装成旧源码。脚本保留已匹配的 Windows 文件，只上传缺少的 APK 和新的客户端专用校验文件。

本次 `v0.4.1` 属于这种补齐：原 Windows EXE、ZIP、源码 ZIP、旧 `SHA256SUMS-v0.4.1.txt` 和 tag 均保留；Android 使用原 mobile 生产代码、`--build-name=0.4.1 --build-number=2`，默认连接现有云端。Android 界面与功能范围以 mobile 客户端实际实现为准。

## Windows 更新源仍需独立发布

GitHub 联合发布完成后，未来 Windows 新版还应通过 `scripts/publish_desktop_update.py` 上传到签名更新源，步骤见 [desktop-updates.md](desktop-updates.md)。仅上传 GitHub 附件不会让 Windows 更新器发现新版。GitHub 发布与更新源激活没有跨服务事务；两边均成功后再报告客户端发布完成。

健康手表导入、后台同步和系统推送仍需凭证及真机联调。不能将 APK 构建、签名成功或 Flutter 测试当作这些能力已经真机验收。
