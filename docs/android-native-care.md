# 主动关心与 Android 免费基础通知

主动关心会先保存为当前用户的会话。Windows、Web、Android 登录后都可在「陪伴收件箱 → 主动关心」查看、标为已读并接着回复；没有需要注意的事情不会为了打招呼而发消息。Android 应用内每 30 秒刷新一次，回到前台也会刷新。模型不可用时，已保存的用户回复显示待回应，重试复用 request_id，避免重复发送。

## 管理员配置与用户授权

1. 管理员在网页或 Android「我的 → 管理关心频率、模型与通知服务」配置真实模型服务、开启定期分析并保存间隔、冷却时间、每天上限。普通用户没有管理入口，后端每次请求仍会验证权限。
2. 每位用户在自己的关心收件箱明确同意后开启主动关心。授权会允许模型处理必要摘要，用于聊天与主动关心；停止关心只停止主动分析，不撤销普通聊天的模型授权。免打扰和发信频率限制仍适用。
3. 当前云部署复用每小时调度，分析间隔最短 60 分钟且为 60 的整数倍；例如 120 分钟约每两轮检查一次，实际时间可能因调度延迟而变化。自托管 Python 服务可配置更短周期，界面按后端返回的最小值和调度周期解释。
4. 应用内消息不依赖 Firebase 或极光账号。应用外通知另行使用以下极光通道；没有配置、没有系统权限或用户关闭通知时，收件箱仍正常工作。

## 极光免费基础推送

代码使用极光 JPush 基础 REST API，不自动订购任何付费套餐。请创建极光应用并使用账号现有免费权益；实际免费额度、厂商资质要求、权益变动以[极光控制台](https://www.jiguang.cn/)为准。AI 模型调用、服务器与第三方账号费用不属于推送免费权益。

1. 在极光创建 Android 应用，包名填 `com.changban.companion_mobile`。记录 **AppKey** 与 **MasterSecret**；如控制台要求签名摘要，使用实际发布 APK 的证书信息。当前 APK 为明确标注的调试签名，不能当成正式应用商店签名。
2. 在小伴管理员「Android 应用外通知」选择极光、填 AppKey/MasterSecret，启用并保存。MasterSecret 留空保留原值，必须勾选清除才删除；服务端加密保存，不回显、不下发给用户，不进入 Git/APK。
3. 用户在 Android「我的 → 系统通知 → 开启系统推送」同意系统通知授权。本机此时才初始化 JPush、获得 registration ID，并绑定到当前登录账号。退出登录会解绑当前设备并停止 SDK，切换账号必须重新开启。管理员设置不会替代用户授权。
4. 管理员可在自己的 Android 注册完成后，点击「向我已注册的设备发测试通知」。这是向当前管理员自己设备的显式测试；返回已提交只说明推送平台接受请求，必须在真机确认送达。

通知只携带通用提醒和 care_id，个人摘要与聊天正文从登录后的服务接口读取。点击通知按 care_id 打开当前账号可访问的会话；客户端不执行通知中的 URL。前台只刷新收件箱，不额外创建一条本地通知；系统通知由原生 SDK 处理。通知频道为 `xiaoban_care`。

## 国内厂商与 GMS 通道

APK 捆绑官方 JPush Flutter **3.6.0**、Android JPush **6.3.0**、JCore **5.6.0**，以及 Huawei/Xiaomi/OPPO/vivo/Honor/FCM 六类扩展。SDK 文件存在不代表厂商服务已开通。极光后台仍需完成相应厂商认证、包名/签名匹配以及服务端凭证配置；需要的服务账号私钥只能保存在服务端或极光控制台，不能填入下列客户端 JSON。

管理员高级配置 `client_options` 格式：

```json
{
  "firebase": {
    "api_key": "",
    "app_id": "",
    "messaging_sender_id": "",
    "project_id": ""
  },
  "manufacturers": {
    "huawei_app_id": "",
    "xiaomi_app_id": "",
    "xiaomi_app_key": "",
    "oppo_app_key": "",
    "oppo_app_secret": "",
    "vivo_app_id": "",
    "vivo_app_key": "",
    "honor_app_id": ""
  }
}
```

这些是手机 SDK 需要的公开客户端配置，已登录客户端可读取。FCM 四项来自 Android Firebase 应用的公开配置，不是服务账号 JSON。小米、OPPO 的 SDK 客户端值同样会进入设备，禁止填服务器推送凭证。

| 配置 | 生效方式 |
| --- | --- |
| 极光 AppKey | 首次授权后运行时初始化；已初始化后换应用需重启小伴再开启通知 |
| Firebase 公开四项 | 运行时 FirebaseOptions；更换已初始化的项目需重启，若包内固定其他项目需重新打包 |
| 小米 App ID/Key、OPPO 客户端 Key/Secret | JPushConfig 运行时设置，重新启动/开启通知后应用 |
| 华为 App ID、vivo App ID/Key、荣耀 App ID | 当前官方 SDK 依赖 Manifest；管理员保存后需重新构建 APK，单改网页不能启用这些通道 |

没有 Google 服务的设备可使用极光直连和已正确开通的国内厂商通道；GMS 设备可额外使用 FCM。厂商强制停止、关闭自启动/后台活动、网络不可达和免费权益限制都可能影响送达，不承诺杀进程后必达。

## 带公开厂商标识重新构建

先把服务器保存的四个静态客户端值单独写入仓库外的 JSON 文件（不是整个推送配置导出）：

```json
{
  "huawei_app_id": "",
  "vivo_app_id": "",
  "vivo_app_key": "",
  "honor_app_id": ""
}
```

```powershell
./scripts/build-android.ps1 -AppVersion 0.5.0 -BuildNumber 3 `
  -PreviousApk '上一正式版本的实际APK绝对路径' `
  -PublicPushClientConfig '仓库外的public-push.json绝对路径'
```

后续版本必须递增 versionCode。构建脚本只接受上面四个字段，拒绝 MasterSecret/API Key/未知字段；通过临时 Gradle 环境注入并在退出时恢复环境。报告会从实际 APK 验证 Manifest 四项、六类扩展 DEX、通知频道，以及不存在重复 FlutterFire FCM handler。构建快照 `push-client-build-inputs.json` 只保存这些公开标识。没有文件时四项均为空，基础关心和极光直连仍能按配置工作。

华为如需其项目额外配置，请遵循官方 Push Kit/极光集成流程，不能用未经验证的占位参数声称已经支持离线送达。厂商账号/服务端凭证未提供的本次交付，报告明确 `real_device_delivery_verified: false`；编译、签名和单元测试不能替代不同品牌真机的前台、后台、冷启动点击验收。

参考：[官方 Flutter SDK 与版本记录](https://pub.dev/packages/jpush_flutter/changelog)、[JPush Android API](https://docs.jiguang.cn/jpush/client/Android/android_api)、[极光厂商集成指南](https://docs.jiguang.cn/jpush/client/Android/android_3rd_guide)、[荣耀官方集成指南](https://developer.honor.com/en/doc/guides/101080)。客户端发布仍执行 [client-releases.md](client-releases.md) 的 Windows/Android 联合门禁；Android 附件上传不代表实现 Android 应用内自动更新。
