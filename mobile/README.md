# 小伴 · iOS / Android

Flutter 客户端包含登录/注册、独立演示、今日指标与趋势、时间线、饮食/饮水/运动/睡眠/步数/心情手录、记忆增改删、带依据的对话、提醒收件箱及反馈、免打扰设置、健康数据授权导入和可选系统推送。

演示也需要运行本仓库后端；“独立”表示隔离的合成数据账号。没有外部模型、Open Wearables 或 Firebase 凭证时，手录、本地规则陪伴和收件箱仍可用。未配置集成不会假称已连接。

## 启动

要求 Flutter stable / Dart >=3.9.2；本次使用 Flutter3.47.9 / Dart3.13.7。Android 最低 API29，JDK17+、platform36、build-tools36；iOS15+ 需要 macOS、Xcode 和 CocoaPods。

先按根目录 README 启动陪伴后端，再执行：

```sh
cd mobile
flutter pub get
python tool/configure_platforms.py
flutter devices
flutter run -d YOUR_DEVICE_ID
```

原生工程已经用官方 `flutter create . --platforms android,ios --org com.changban --project-name companion_mobile --no-pub` 生成并保存在仓库。脚本在工程缺失时调用同一生成器，再幂等配置权限。macOS 可用 `python3`；iOS 使用 CocoaPods，已在 pubspec 中关闭 Swift Package Manager，以兼容健康 SDK 的 podspec。

默认地址：Android 模拟器 `http://10.0.2.2:8765`；iOS 模拟器 `http://127.0.0.1:8765`。真机请在登录页展开“服务地址”，填写后端电脑的局域网 IP 或 HTTPS 域名。局域网调试时后端需监听 `0.0.0.0`，手机和电脑需能互相访问。也可设首次默认值：

```sh
flutter run -d YOUR_DEVICE_ID --dart-define=COMPANION_URL=http://192.168.1.20:8765
```

已保存地址优先；退出登录后可切换服务。Android 仅 debug manifest 允许 HTTP；iOS 仅 Debug 的 `Info-Debug.plist` 允许 HTTP。Profile/Release 保留 ATS 限制，Dart 正式构建也强制 HTTPS。

## Android APK

从 [GitHub v0.4.1 Release](https://github.com/xiaopengqiu/xiaoban-companion-agent/releases/tag/v0.4.1) 下载 `Xiaoban-Android-debug.apk`。该包使用完整 Flutter / 健康 SDK / Firebase 依赖，是调试签名的试用版，最低 Android10（API29），默认服务地址为 `https://xiaoban-companion.abloom-haven-3343.chatgpt.site`。已经保存的服务地址优先；需要切换时在登录页展开“服务地址”。

当前 Android 原生界面覆盖基础登录/注册、聊天、记录、记忆和提醒，尚未同步 Web/Windows 后续全部近期处境、事情跟进和管理员界面。GitHub 同步上传 APK 不代表 Android 已实现应用内自动更新；系统安装和真实健康同步、推送仍需各自验收。

在仓库根目录执行可重复构建入口：

```powershell
./scripts/build-android.ps1 -AppVersion 0.4.1 -BuildNumber 2 -PreviousApk artifacts/companion-android-debug.apk
```

`BuildNumber` 必须显式提供并高于上一 APK 的实际 `versionCode`。后续发布应将 `-PreviousApk` 指向上一个已发布 APK，保留相同签名身份。脚本执行 Flutter分析、11项源码测试、真实APK构建，再用 Android build-tools 读取包名/版本/SDK并验证v2签名；APK中的默认云地址、前后生产源码指纹也需一致。输出到 `artifacts/android/<版本>/Xiaoban-Android-debug.apk` 与 `build-report.json`，全部通过后才生成报告。

构建通过标准 `--build-name` / `--build-number` 设置0.4.1+2，不改写历史 tag 中 `pubspec.yaml` 的版本。默认 `PublicAppUrl` 指向小伴云端，可显式使用其他 HTTPS 地址。`-FlutterPath`、`-AndroidSdk`、`-JavaHome`、`-OutputDir` 可覆盖本机工具或输出路径。脚本临时选择空闲ASCII盘符，先用 `flutter clean` 清理当前项目生成输出，避免上次盘符写入的绝对路径缓存污染本次构建；全局 SDK、Pub 和 Gradle 缓存保留。退出时只移除自己创建且仍指向当前mobile目录的映射。

后续 Windows / Android 必须通过统一客户端发布门禁，步骤见 [docs/client-releases.md](../docs/client-releases.md)。它会拒绝缺少一端的安装包、版本/摘要不匹配或签名不连续的发布。正式商店分发仍需要正式签名、HTTPS服务及相应商店声明，不把调试签名包当作已上架产品。

本次实际包信息和验证记录见 [VERIFICATION.md](VERIFICATION.md)。旧本地0.1.0+1调试APK保留在忽略的 `artifacts/companion-android-debug.apk`，其摘要为 `18afa3c35a4b75122ba899ec1d1370039f7b34e525431285b36173200fe578d6`，不将旧文件重命名为新版。

本次 Windows 已安装的工具：

```powershell
$env:JAVA_HOME = 'C:\Program Files\Java\jdk-21'
$env:ANDROID_HOME = Join-Path $env:LOCALAPPDATA 'CodexTools\AndroidSdk'
$env:ANDROID_SDK_ROOT = $env:ANDROID_HOME
$env:Path = (Join-Path $env:LOCALAPPDATA 'CodexTools\flutter\bin') + ';' + $env:Path
flutter build apk --debug
```

无需安装模拟器即可生成 APK。最新 Android CLI 用 `android --sdk SDK_PATH sdk install platforms/android-36 build-tools/36.0.0 ndk/28.2.13676358` 安装组件；旧 sdkmanager 分号包名在本次 CLI 不兼容，所以手动预装 Flutter 所需 NDK。构建需能访问 Gradle、Google Maven、Maven Central、JitPack 和 Flutter 下载站，上游健康原生 SDK 来自 JitPack。

## 健康数据

先按根目录部署文档配置 Open Wearables 服务及**仅服务端保存的**管理员 key，再在手机“我的”点“授权并连接”。客户端请求后端绑定当前用户并发放 scoped access/refresh token，执行 SDK `configure` → 清除旧会话 → `signIn` → Android provider 选择 → `requestAuthorization` → `startBackgroundSync(syncDaysBack:30)`。只请求步数、睡眠、运动、饮水和心率。

真机部署时，`OPEN_WEARABLES_PUBLIC_URL` 应为手机可访问的上游 origin（不附 `/api/v1` 路径，也不要使用手机自己的 `127.0.0.1`）。SDK 自行拼接上传/刷新路径。后端返回的 raw JWT 可直接传给 SDK；已核实 Android v0.13.1 和 iOS0.16.0 原生代码会为 Authorization 自动补 `Bearer `，不会重复添加。

Android 优先 Health Connect，否则用可用的 Samsung Health；已配置 FlutterFragmentActivity、permission aliases、读权限和包可见性。后台同步显示可见的通知，health foreground service 由 SDK manifest 合并；上架时需在 Play Console 声明健康前台服务。Samsung 测试可能需在 Samsung Health 开发者模式授权。

iOS 已加入 HealthKit / background-delivery entitlement、用途文案和后台任务 identifiers。用 Xcode 打开 `ios/Runner.xcworkspace`，为 Runner 选择自己的 Signing Team 和唯一 Bundle ID，确认 HealthKit / Background Delivery capability。系统返回的可能只是部分授权类型，不把授权弹窗完成当成数据全部可用。

手机先同步到 Open Wearables，稍后点“从服务导入”，陪伴服务才拉取归一化记录。首次同步可能需要等待，零导入不代表用户没活动。饮食通过用户描述记录，不臆测热量。手录时间从手机本地转换为 UTC 上传；统计与免打扰按账号 IANA 时区计算。

陪伴会话仅放在 Flutter Secure Storage；健康 SDK 自行安全保存 scoped token。客户端没有管理员 key 输入框，也不能任填别人的上游 userId。退出先停止本机健康同步、清除原生会话并使旧 FCM token 失效，避免共享设备切换身份后继续投递给旧账号。

## 可选 Firebase / APNs

默认 `ENABLE_PUSH=false`，无需 Firebase 即可启动，提醒仍在应用收件箱。启用需完成：

1. 同一个 Firebase 项目下注册 Android/iOS 应用。Android 包名 `com.changban.companion_mobile`；iOS 使用 Xcode 最终 Bundle ID。放置 `android/app/google-services.json`，Gradle 仅在文件存在时应用 Google Services 插件。
2. iOS 把 `GoogleService-Info.plist` 加入 Runner target 的 Copy Bundle Resources。**仅放 plist 不足以开启 APNs**：Runner → Signing & Capabilities 添加 Push Notifications，确认 Apple App ID / provisioning profile 有推送授权，Background Modes 包含 Remote notifications。
3. 在这个 Firebase 项目的 Cloud Messaging 设置上传对应 Apple 团队的 APNs authentication key / 合适证书，核对 Team ID、Key ID、Bundle ID。后端服务账号也属于同一个 Firebase 项目，私钥只放服务器。
4. `aps-environment` 必须匹配实际 provisioning profile：开发签名通常 `development`，TestFlight / App Store 通常 `production`，以 profile 为准，不仅凭 Debug/Release 猜测。工程基础版默认无此 entitlement；脚本显式配置并保留现有值：

   ```sh
   python tool/configure_platforms.py --ios-push-environment development
   # 使用分发 profile 时改传 production；关闭可传 --disable-ios-push
   ```

   脚本只改工程，不能代替 Apple App ID / 签名授权。

5. 使用 `flutter run --dart-define=ENABLE_PUSH=true`，在“我的”点“开启系统推送”。服务、原生配置、权限或设备 token 任一缺失都会说明原因。已授权用户重启后恢复 Firebase、token 注册和前台消息监听，不重复弹权限请求。

Android 前台消息刷新收件箱，后台 notification payload 由系统显示；iOS 按已授权限显示。注册 token 不等于用户已收到通知。健康后台同步和 FCM/APNs 送达仍需真机、网络和凭证验收，Windows 无法验证 APNs。

## 数据与模型

“允许外部模型处理必要摘要”默认关闭，显式开启 `preferences.model_consent` 后，已配置的大模型 / Mem0 才可处理必要个人摘要；关闭时使用本地规则。合成数据与本地回复明确标记，观察和记忆可展开依据。

删除账号会删除陪伴数据和已绑定的 Open Wearables 用户档案；不删除手机系统健康 App 的数据。

## 验证与边界

```sh
flutter analyze --no-pub
flutter test
# 可选真实 HTTP 验收：只创建并最终删除自己的独立演示账号
dart run tool/smoke_api.dart http://127.0.0.1:8765
```

11项测试覆盖中文 UTF8、Bearer 请求、结构化错误、HTTPS/数据格式、SDK 凭证完整性、注册健康授权、独立演示、390px 五页渲染、实际手录 JSON/UTC、模型摘要授权默认关闭和保存，以及外部记忆失败待重试状态。

本次还通过 Dart 客户端对运行中的真实后端完成 demo→dashboard→event→memory更正→chat→preferences→reminder评估/反馈→删除测试数据的 HTTP 验收。

本次 Flutter3.47.9 的 analyze 在中文工作区触发上游 LSP 初始化异常。同一 mobile 目录映射 ASCII 盘符后正常：先确认 `V:` 未占用，`subst V: FULL_MOBILE_PATH`，在 `V:\` 执行；完成后 `subst V: /D` 解除。映射不复制源码。

Android 配置关闭了 Kotlin incremental cache，避免 Windows 映射盘工程与 C: Pub Cache 源文件跨盘符时的 `this and base files have different roots` 异常。保留完整健康 SDK / Firebase 依赖，使用正常 Kotlin 编译。

尚需真机与凭证验收：部分健康权限拒绝、后台续传、真实上游绑定与去重、FCM/APNs 前后台投递、跨账号切换、iOS 签名归档。源码测试不代表这些已经通过。

官方参考：[Flutter](https://docs.flutter.dev/install/archive)、[Open Wearables SDK0.0.26](https://pub.dev/packages/open_wearables_health_sdk/versions/0.0.26)、[Android CLI](https://developer.android.com/tools/agents/android-cli)、[Firebase FCM](https://firebase.google.com/docs/cloud-messaging/flutter/client)。
