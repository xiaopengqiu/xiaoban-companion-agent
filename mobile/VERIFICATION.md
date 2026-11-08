# Android 0.4.1+2 验证记录 · 2026-11-08

正式 GitHub 附件为 `Xiaoban-Android-debug.apk`，调试签名。生产移动端源码对应现有 `v0.4.1` tag 的 `01ab32f78ed94a258f1fd4fc251e363a771866f8`，通过 Flutter 标准构建参数设置版本，不修改历史 tag、`pubspec.yaml` 或移动端功能源码。构建依赖为原有完整 Flutter/健康 SDK/Firebase 环境。

| 项目 | 实际核验结果 |
|---|---|
| versionName / versionCode | `0.4.1` / `2`，旧本地 APK 为`0.1.0` / `1` |
| package | `com.changban.companion_mobile` |
| minSdk / targetSdk | `29` / `36` |
| APK bytes | `177,902,374` |
| APK SHA256 | `f332822bd927604339555a1cbbf7cb331564ea6e3b54676e530f46d3444ddeb0` |
| APK Signature Scheme v2 | `apksigner verify --verbose --print-certs` 成功，单一签名者 |
| 签名证书 SHA256 | `2c334e7655652be4946bd75ab66b71a9871106de1760b4439bb20e05f641b4c5`，与旧本地 APK 相同 |
| 默认服务地址 | `https://xiaoban-companion.abloom-haven-3343.chatgpt.site`，实际 APK 的 Flutter kernel 包含该地址 |

本次发布 APK 首次在空闲 ASCII 工作盘 `W:` 上成功构建：Flutter静态分析无问题，11项Flutter测试通过，`assembleDebug` 成功（67秒）。测试覆盖中文UTF8、Bearer请求、结构化错误、HTTPS限制、健康凭证、注册授权、390px五个页面、实际手录请求及模型摘要授权设置。它不是把旧0.1.0 APK改名；Android build-tools 实际读取的新版本与 versionCode 均已验证。

`artifacts/android/0.4.1/build-report.json` 保存实际摘要、证书、平台、SDK、默认地址、上一版本号、源码提交以及构建前后生产输入摘要。`lib/`、原生Android工程及 `pubspec.yaml` / lock 的输入未变化；报告涵盖已跟踪和未忽略的新生产文件，排除凭证及生成缓存。私有签名keystore、用户账号、服务端密钥均未进入源码或新增客户端资源。

另外以 Dart 源客户端对公开HTTPS服务执行 `dart run tool/smoke_api.dart https://xiaoban-companion.abloom-haven-3343.chatgpt.site`，通过独立合成账号的 demo、dashboard、event、memory更正、chat、preferences、reminder 流程，并删除该测试账号。这验证源客户端与云端API兼容，不代表已在真机运行APK。

## 构建入口可靠性

新增 `scripts/build-android.ps1` 和只读检查器 `scripts/android_build_report.py`。Android构建与报告单元/边界回归15项通过，先观察缺失功能失败，再实现并确认通过，覆盖包名/版本/SDK不符、旧版本号、签名不连续、签名验证失败、错误默认地址、中文路径及构建输入校验。

实际复查还发现并修复两个工具链边界：Windows `aapt` 对包含中文的绝对APK路径报 `Illegal byte sequence`，检查器改用Unicode工作目录与ASCII附件名执行，实际新旧APK均通过；Flutter depfile保留上次临时盘符的绝对输出路径，盘符解除后会让Gradle创建目录时失败。入口现在先由Flutter自身清理当前项目生成输出，再恢复依赖，不清理全局SDK/Pub/Gradle缓存。盘符清理由 `QueryDosDeviceW` 核实Unicode目标，只移除自身创建的映射。

修复后完整入口在独立目录 `artifacts/android/0.4.1-rebuild-check` 实跑通过：项目clean、pub get、分析无问题、11项测试、构建（27.1秒）、实际APK metadata/signature/default URL/source检查及报告全部通过。退出后用Windows API确认自身V/W临时映射均无残留。该验证包未替换正式发布APK；调试包重复构建可能因工作路径或归档信息产生不同摘要，不将重建文件覆盖已发布附件。

正式候选及原有 `artifacts/companion-android-debug.apk` 均保留。构建与报告文件位于忽略的 `artifacts/`，代码和本记录进入仓库。

## 验证范围

Android原生界面仍是基础登录、聊天、记录、记忆与提醒客户端；Web/Windows后续全部事情跟进、近期处境及管理员界面没有通过本次运维发布自动移植。本轮没有真机、真实手表、Health Connect后台续传或FCM/APNs送达验收，也没有商店上架或Android应用内自动更新。调试签名的连续性及版本号递增只验证覆盖安装所需的包条件，没有将其宣称为实际真机安装测试。
