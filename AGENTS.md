# 小伴仓库协作与发布规则

用户要求：后续客户端发布必须同时提供 Windows 和 Android 安装包。Web 单独部署、纯文档提交不要求另发客户端版本。

## 客户端发布

- 使用 `scripts/build-windows.ps1` 和 `scripts/build-android.ps1` 构建并完成各自验证。Android 使用明确的发布版本和递增 versionCode；不得只重新命名旧 APK 来冒充新构建。
- 使用 `scripts/publish_clients.py` 统一检查并上传客户端附件；任何平台的包、报告、签名或摘要验证缺失都停止发布。正式 Release 至少包含 Windows EXE、Windows portable ZIP、Android APK 和客户端 SHA256 校验清单。
- Android 的调试签名状态必须在附件名和说明中明确标识。保留现有签名身份以支持覆盖安装；不得悄悄更换签名。正式签名私钥、调试 keystore 和凭证均不进入 Git 或安装包。
- Release 版本应与两端编译后的版本一致；Android 可以通过 Flutter 的 `--build-name` 和 `--build-number` 设置，不必改写已发布 tag 的源码。保存实际源码提交、生产输入摘要、构建参数与验证证据；不能把后来的功能改动伪装成旧 tag 的构建。
- 已发布的同名文件及 tag 不覆盖。补齐旧版本的平台附件时，先核实生产源码与旧 tag 一致，保留所有原有附件；新增独立客户端校验文件，不覆盖旧的校验文件或源码包。
- 新版本先准备 draft Release，通过统一门禁上传并核对两端远端摘要后，再公开发布。现有公开 Release 补附件可直接使用同一门禁，保留原有内容。
- GitHub 客户端发布与 Windows 签名更新源发布是独立步骤。未来版本还需使用 `scripts/publish_desktop_update.py` 发布 Windows feed；仅上传 GitHub 不会自动激活 Windows 更新。不要把 Android 附件上传宣称为 Android 应用内自动更新。
- 发布说明明确各平台的实际功能和验证范围。APK 构建、签名校验与源码测试不能代替真实手表、真机后台同步或系统推送验收。

完整操作步骤见 `docs/client-releases.md`；Windows 更新协议见 `docs/desktop-updates.md`。
