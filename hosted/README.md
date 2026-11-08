# 小伴云端运行时

这是独立的 Cloudflare Worker + D1 服务，复用主项目 `web/` 界面和 `/api` 契约。账号、健康记录、记忆、对话与提醒存入服务端 D1；浏览器只保留登录令牌。它使用确定性的 JavaScript 策略适配器，不在 Worker 中运行 Python、LangGraph 或 Mem0。配置并经个人明确授权后，可调用兼容 Chat Completions 的外部模型。

```powershell
cd hosted
npm ci
npm test
npm run build
```

构建优先读取 `WEB_SOURCE_DIR`，其次寻找 `../web`、`./web`。静态文本资产和云端运行时标记嵌入 `dist/server/index.js`；该文件 default-export `fetch` 和 `scheduled`。`.openai/hosting.json` 保持 Site ID，设置 `"d1":"DB"`，省略 `static`，不放凭证。完整源码含 `worker/`、`scripts/`、`db/`、`drizzle/`、锁文件和静态界面源文件。

D1 的 0000–0002 迁移由 `db/schema.js` 经 Drizzle 生成，SQL 和 `drizzle/meta/` 一起入库。追加 schema 变更运行 `npm run db:generate`，不要修改已应用的 SQL。Sites 打包流程把 migrations 放进 `dist/.openai/drizzle` 并在上传 Worker 前应用；请求处理程序不执行 CREATE/ALTER TABLE。测试在真实 Miniflare D1 上应用全部 migrations，还验证 Worker 完整重启后的持久性。

必须通过 Sites 服务端 secret 配置 `AUTH_PEPPER`（至少 32 个字符）。缺失时注册和登录返回 503。密码先经服务端 HMAC-SHA256 pepper 预处理，再使用随机独立 salt 和 PBKDF2-SHA256 100,000 次；这是 Workers WebCrypto 的迭代限制，区别于 Python 版本的 600,000 次。会话仅保存随机 bearer token 的 SHA-256，30 天到期，退出会撤销当前会话。匿名身份只采用平台覆盖的 `CF-Connecting-IP`，不信任转发头；限流状态保存在 D1。

可选服务端配置：

- 模型：`COMPANION_MODEL_BASE_URL`、`COMPANION_MODEL_API_KEY`、`COMPANION_MODEL`。个人 `model_consent` 默认 false。模型只获得有界且经用户范围过滤的上下文。
- 穿戴：`OPEN_WEARABLES_URL`、`OPEN_WEARABLES_API_KEY`、`OPEN_WEARABLES_APP_ID`、`OPEN_WEARABLES_APP_SECRET`、`OPEN_WEARABLES_PUBLIC_URL`。主机必须 HTTPS，管理员 key 不下发客户端；SDK token 由服务器绑定的用户 UUID 签发。同步回看 30 天，以 D1 batch 事务导入。
- Webhook：`OPEN_WEARABLES_WEBHOOK_SECRET`，接收 `/api/integrations/open-wearables/webhook` 原始 Svix 签名请求。持久收据幂等，先反查绑定再拉取权威记录。删除记录留下来源屏蔽键，阻止重导；不修改手机健康平台里的原始数据。
- FCM：`FCM_SERVICE_ACCOUNT_JSON`。推送目标只由服务器选择；锁屏文案不包含健康细节。`queued` 表示 FCM 接受请求，不代表设备已显示。没有凭证时只进入消息箱。
- 调度：`CRON_TOKEN`（至少 32 个字符）保护 `POST /api/internal/evaluate`；也支持 Workers `scheduled` handler。导出 handler 不会自动开通定时器，必须另行配置平台或可信调度器。当前网页明确说明定时评估尚未开启。每次默认处理 2 个账号，持久游标轮转；`SCHEDULER_BATCH_SIZE` 可设置为 1–30，需先确认部署的 D1 查询预算。

云端试运行边界：请求体流式限制 64KB；事件 metadata 最多 4KB；每账号最多 2,000 条事件，达到限额会明确拒绝新增而不静默丢弃；最多注册 5 个推送设备。匿名合成体验最多 300 个账号、每 IP 每小时 5 次，7 天清理；普通账号不受体验账号数量限制。自动策略覆盖睡眠和活动，饮食/饮水/心情通过已有真实记录回答，不估算未记录的摄入或诊断健康状态。

上游和 FCM 网络传输均有总截止时间，拒绝 302/307 自动转发凭证。D1 租约、按用户本地日的预算、免打扰、过期重验、反馈与证据撤销适用于云端。跨请求租约和来源键持久化，不依赖单个 Worker isolate 的内存。原始健康数据不放入浏览器 localStorage。公开部署仍应监控配额、滥用和运维告警；尚未验证真实手表、实际模型或真实 FCM 凭证时，不将模拟运输测试称为真机验证。
