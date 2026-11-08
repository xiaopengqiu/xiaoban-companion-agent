# 小伴云端运行时

这是独立的 Cloudflare Worker + D1 服务，复用主项目 `web/` 界面和 `/api` 契约。账号、健康记录、记忆、对话与提醒存入服务端 D1；浏览器只保留登录令牌。它使用确定性的 JavaScript 策略适配器，不在 Worker 中运行 Python、LangGraph 或 Mem0。配置并经个人明确授权后，可调用兼容 Chat Completions 的外部模型。

v0.3 增加个人理解和重要事情：记忆可分为偏好、目标、生活事实、近期处境、关系与陪伴边界；近期处境默认七天有效，可更正、续期或明确改为长期。过期内容不进入模型、健康提醒或此前对话上下文。日常分享只提供待确认入口，不自动保存推断出的事实。用户可选择先倾听、一起理清或讨论行动步骤。

`/api/threads` 保存用户确认的重要事情，支持进行中、暂停、完成以及后续结果；跟进默认为关闭，开启须选择明确时间。到期邀请与健康提醒共享每日预算、免打扰和全局开关，完成、撤销或修改会使旧提醒失效。每账号最多100件重要事情，支持删除与账号级联清理。平台定时器仍需另外配置，不能把新增跟进时间视为云端无人值守调度已开启。

```powershell
cd hosted
npm ci
npm test
npm run build
```

构建优先读取 `WEB_SOURCE_DIR`，其次寻找 `../web`、`./web`。静态文本资产和云端运行时标记嵌入 `dist/server/index.js`；该文件 default-export `fetch` 和 `scheduled`。`.openai/hosting.json` 保持 Site ID，设置 `"d1":"DB"`，省略 `static`，不放凭证。完整源码含 `worker/`、`scripts/`、`db/`、`drizzle/`、锁文件和静态界面源文件。

D1 的 0000–0003 迁移由 `db/schema.js` 经 Drizzle 生成，SQL 和 `drizzle/meta/` 一起入库。0003追加用户范围内的 `threads` 表；旧用户偏好自动获得陪伴方式和重要事情跟进的默认值。追加 schema 变更运行 `npm run db:generate`，不要修改已应用的 SQL。Sites 打包流程把 migrations 放进 `dist/.openai/drizzle` 并在上传 Worker 前应用；请求处理程序不执行 CREATE/ALTER TABLE。测试在真实 Miniflare D1 上应用全部 migrations，还验证 Worker 完整重启后的持久性。

必须通过 Sites 服务端 secret 配置 `AUTH_PEPPER`（至少 32 个字符）。缺失时注册和登录返回 503。密码先经服务端 HMAC-SHA256 pepper 预处理，再使用随机独立 salt 和 PBKDF2-SHA256 100,000 次；这是 Workers WebCrypto 的迭代限制，区别于 Python 版本的 600,000 次。会话仅保存随机 bearer token 的 SHA-256，30 天到期，退出会撤销当前会话。匿名身份只采用平台覆盖的 `CF-Connecting-IP`，不信任转发头；限流状态保存在 D1。

可选服务端配置：

- 模型：`COMPANION_MODEL_BASE_URL`、`COMPANION_MODEL_API_KEY`、`COMPANION_MODEL`。个人 `model_consent` 默认 false。模型只获得有界且经用户范围过滤的上下文。
- 穿戴：`OPEN_WEARABLES_URL`、`OPEN_WEARABLES_API_KEY`、`OPEN_WEARABLES_APP_ID`、`OPEN_WEARABLES_APP_SECRET`、`OPEN_WEARABLES_PUBLIC_URL`。主机必须 HTTPS，管理员 key 不下发客户端；SDK token 由服务器绑定的用户 UUID 签发。同步回看 30 天，以 D1 batch 事务导入。
- Webhook：`OPEN_WEARABLES_WEBHOOK_SECRET`，接收 `/api/integrations/open-wearables/webhook` 原始 Svix 签名请求。持久收据幂等，先反查绑定再拉取权威记录。删除记录留下来源屏蔽键，阻止重导；不修改手机健康平台里的原始数据。
- FCM：`FCM_SERVICE_ACCOUNT_JSON`。推送目标只由服务器选择；锁屏文案不包含健康细节。`queued` 表示 FCM 接受请求，不代表设备已显示。没有凭证时只进入消息箱。
- 调度：`CRON_TOKEN`（至少 32 个字符）保护 `POST /api/internal/evaluate`；也支持 Workers `scheduled` handler。导出 handler 不会自动开通定时器，必须另行配置平台或可信调度器。当前网页明确说明定时评估尚未开启。每次默认处理 2 个账号，持久游标轮转；`SCHEDULER_BATCH_SIZE` 可设置为 1–30，需先确认部署的 D1 查询预算。

管理员也可以通过 `/api/admin/model-config` 选择 API 或管理员本地 Claude/Codex 网关；普通账号不能修改或测试模型服务。设置加密后保存到 D1 `service_state`，密钥不在公开状态或配置读回中返回。未保存设置时保留上述环境变量的兼容入口。`/api/internal/admin/grant` 仅用于服务端强 secret 保护的一次性管理员初始化，不能从注册表单传入管理员身份。云端“本地模式”连接的是管理员机器上的 HTTPS 网关，需要该机器、CLI 登录和网关保持运行，并非访问者浏览器里的本地模型。个人处理授权仍然独立生效；未配置或调用失败时明确回到规则模式。

管理员可用 `GET /api/admin/users` 查看最近100个账号摘要，或传 `?email=` 精确筛选；`POST /api/admin/users/{id}/role` 仅接受 `{is_admin:boolean}` 授权或撤销权限。演示账号不能成为管理员，普通账号不能调用这些接口。角色写入时在同一 SQL 中重验操作者权限及至少保留一名管理员；账号删除也有最终原子保护，正常的最后管理员请求在任何上游清理前被拒绝。

云端试运行边界：请求体流式限制 64KB；事件 metadata 最多 4KB；每账号最多 2,000 条事件，达到限额会明确拒绝新增而不静默丢弃；最多注册 5 个推送设备。匿名合成体验最多 300 个账号、每 IP 每小时 5 次，7 天清理；普通账号不受体验账号数量限制。自动策略覆盖睡眠和活动，饮食/饮水/心情通过已有真实记录回答，不估算未记录的摄入或诊断健康状态。

上游和 FCM 网络传输均有总截止时间，拒绝 302/307 自动转发凭证。D1 租约、按用户本地日的预算、免打扰、过期重验、反馈与证据撤销适用于云端。跨请求租约和来源键持久化，不依赖单个 Worker isolate 的内存。原始健康数据不放入浏览器 localStorage。公开部署仍应监控配额、滥用和运维告警；尚未验证真实手表、实际模型或真实 FCM 凭证时，不将模拟运输测试称为真机验证。
