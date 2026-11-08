# 验证记录

日期：2026-11-08（Asia/Shanghai）。所有自动测试使用合成数据；没有上传用户的真实健康信息。

## 已运行

| 检查 | 结果 | 覆盖范围 |
| --- | --- | --- |
| `.venv/Scripts/python -m pytest -q` | 71 项通过 | API、策略、Open Wearables 网络边界、记忆索引和实际 Mem0 包契约 |
| `node --test tests/test_browser_lifecycle.mjs` | 3 项通过 | 跨会话旧响应隔离、首条通知、历史通知不重发 |
| `python -m compileall -q companion` | 退出 0 | Python 源码编译 |
| `node --check web/app.js` | 退出 0 | 浏览器模块语法 |
| `flutter analyze --no-pub` | No issues found | 双端客户端静态分析（Windows 使用同一目录的 ASCII 映射） |
| `flutter test --no-pub` | 11 项通过 | API 中文编码、鉴权、授权默认值、390px 页面、手录、索引失败提示 |
| Dart HTTP smoke | 通过 | 对运行中真实陪伴 API 的独立合成账号全流程 |
| Docker Compose `config --quiet` | 两份配置退出 0 | 本地服务及固定版本上游部署文件的配置解析 |
| `pip check` | No broken requirements found | 当前已安装依赖一致性 |

Mem0 2.2.1 实包验证实际运行 Memory 与本地 Qdrant 的增改删和用户过滤；仅外部 embedding 边界使用确定性 fixture，未测试真实 embedding 的语义质量。Open Wearables 测试通过 httpx.MockTransport 核对固定版本的真实路由、管理 header、SDK token body、游标分页、日期、时长及空值；这不代表实际设备已连接。

独立代码与规格审查发现并修复：跨日稍后提醒预算、发送领取超时恢复、旧聊天复活已删除记忆、记忆超过固定数量后新信息不可见、跨账号异步界面回写、新账号首条浏览器提醒漏发、时间戳及日汇总边界。

补充回归核查了餐食真实备注、饮水单位换算、心情原文、限定范围的模型事件上下文、日期紧贴中文的 30 天查询，以及用户删除事件后同步/重启不重新导入。

## 浏览器人工验证

在运行中的本地服务中，使用独立模拟账号验证：

1. 进入演示，展示 21 天合成记录和统计。
2. 保存偏好，在聊天中找回该偏好。
3. 手动保存餐食，在时间线核对备注与时间。
4. 评估主动提醒，提交完成反馈并确认状态变化。
5. 重启服务，账号、偏好和聊天仍可读取。
6. 聊天时切换页面，回复仍更新当前聊天区；退出后清理个人界面。
7. 1440px 桌面与 390px 手机布局；手机无横向溢出。
8. 浏览器控制台 error 记录为空。

截图保存在忽略的 `artifacts/companion-desktop.png` 与 `artifacts/companion-mobile-web.png`。浏览器系统通知权限未擅自开启，其逻辑由单元测试覆盖。

## 需要真实运行环境的验收

- 实际 iPhone/Apple Watch 与 Android 手环的覆盖指标、授权拒绝、后台延迟、补传及去重。
- 上游 Open Wearables Docker 服务启动、厂商 OAuth 与真实用户数据导入。本机 Docker daemon 查询未得到可用响应；本轮验证部署配置及固定源码，没有声称服务已上线。
- 用户选择的外部模型与 embedding 服务的真实调用效果、费用和供应商处理设置。
- Firebase/APNs 真机前后台送达、权限撤销与共享设备切换。FCM queued 不代表设备确认展示。
- iOS 真正的签名、编译和归档需要 macOS/Xcode；本 Windows 环境未执行。
- Android APK 构建结果在下方单独记录。

当前验证支持交付**本地可运行的开发首版**，不等于已经完成商店上架或生产环境验收。

## Android 构建

`flutter build apk --debug` 已实际成功，退出 0；保留完整 Open Wearables 和 Firebase 插件依赖。生成 `mobile/build/app/outputs/flutter-apk/app-debug.apk`，177,902,350 bytes。交付副本为 `artifacts/companion-android-debug.apk`，使用调试签名，适合本地试用。

本机已配置 Flutter3.47.9、JDK21、Android SDK、NDK28.2.13676358 与 CMake3.22.1。初次构建遇到 Windows ASCII 工程映射盘与 Pub 缓存盘符不同的 Kotlin 增量缓存问题；在 Android 工程及配置脚本中关闭该增量缓存后，真实插件编译通过。该处理没有删除健康或推送功能。

APK 编译成功不替代真机运行与健康权限/后台同步/推送验收；iOS 构建边界同上。
