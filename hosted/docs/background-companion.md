# 浏览器后台陪伴

云端运行检查和设备接收通知是两项独立能力。关闭网页不停止云端检查；接收通知仍依赖浏览器、系统权限、网络和推送服务。

## 使用

1. 用支持 Push API、Service Worker 和 Web Locks 的独立浏览器打开在线小伴并登录。
2. 在“陪伴设置”开启主动陪伴，设置免打扰、每日上限和允许跟进已约定事情。
3. 点击“开启浏览器后台通知”，在浏览器提示中允许通知；每个浏览器/设备分别授权。
4. 点击“发送测试通知”，核实系统实际显示。测试遵守免打扰和主动陪伴开关，每分钟最多一次。
5. 在“在意的事”逐件明确开启跟进并选择时间。云端分批检查到期事项，未约定的事情不会自动联系。

通知只显示“小伴有一条新提醒”等通用文字。点击后进入小伴查看具体内容。关闭网页保留订阅，退出账号会清理当前账号在本浏览器的订阅。“关闭本浏览器通知”只解绑当前端点，不影响其他设备。

浏览器完全退出、系统关机、离线、通知权限被拒绝或厂商省电限制可能阻止送达。Android 可先用支持推送的手机浏览器接收；现有原生 APK 未启用 Firebase，不将 Web Push 宣称为原生 Android 推送。

## 运行与凭证

Sites 发布沿用既有 Site、D1 与 R2，追加 Drizzle 0004 存储订阅。每账号最多5个浏览器设备，唯一端点不得被其他账号直接抢占，删账号级联清理。推送端点仅允许已知服务 HTTPS 地址且拒绝重定向。`@block65/webcrypto-web-push@2.0.0` 提供 RFC8291 加密及 RFC8292 VAPID 签名。

Sites runtime secrets：`WEB_PUSH_PUBLIC_KEY`、`WEB_PUSH_PRIVATE_KEY`、`WEB_PUSH_SUBJECT`。私钥只存服务端，API仅返回公钥。VAPID密钥应持续保留，轮换后旧订阅需要重新授权。

云端使用与该 Site 关联的 Sites 定时任务，约每小时运行一轮，每轮最多8个已有批次；不是 Cloudflare 原生 Cron。每个批次仍使用现有持久锁、账号租约、免打扰、每日预算、失效重验和有限重试。单批默认2账号，超过一轮处理能力时下轮从持久游标续跑，因此不能承诺每个账号准点收到。

公开写端点 `POST /api/internal/evaluate` 保留独立 Bearer 授权。设置启用时，将 `CRON_TOKEN` 保存为 UTF-8 字符串 `xiaoban-scheduler-v1`、一个 NUL 字节及该 Site 支持的 owner service access token 拼接后的 SHA256 小写十六进制；原平台凭证仅通过该 Site 的 `OAI-Sites-Authorization` 发送。未来云任务从 Sites `get_site` 获得服务访问，不在提示词、源码或文件中存放任何凭证。平台凭证缺失/轮换导致401时停止并告知所有者，不自动修改部署密钥。此授权只用于同一已验证 Site HTTPS origin，禁止重定向。

`BACKGROUND_CHECK_INTERVAL_MINUTES=60` 记录预期检查间隔。每次完成保存 `scheduler_health`：时间、处理数量和失败数量；不保存账号信息。普通用户通过集成状态只看到有界能力与心跳，管理员可读取 `/api/admin/background-status`。超过三个检查间隔未完成时界面不再显示运行正常。保存定时任务并不证明实际执行，部署验收必须另做一次新凭证请求并读回持久心跳。

发送成功集合按端点/设备token的SHA256逐个持久保存。失败重试只针对尚未接受的设备，404/410浏览器订阅按账号清理。`queued`仅表示推送服务接受，不等于设备已显示。

## 验证

在 `hosted/` 运行 `npm test` 和 `npm run build`；根目录运行 `node --test tests/test_browser_lifecycle.mjs tests/test_companion_ui.mjs tests/test_web_push_client.mjs`。

单元/Miniflare验证真实加密解密、VAPID验签、账号隔离、设备限额、部分发送与重试、到期跟进、免打扰、无页面调度、持久心跳、订阅退出竞态。自动测试不代替用户设备上的浏览器授权及关闭页面后的真实通知送达。
