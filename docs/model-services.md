# 管理员模型服务

小伴支持两种调用方式，由管理员统一配置。普通用户不能读取密钥、修改模型端点、切换服务或授权角色；每位用户仍可独立决定是否允许模型处理自己的必要上下文。

## API 模式

登录管理员账号，在“陪伴设置”的管理员区域选择 API 模式，填写兼容 Chat Completions 的 HTTPS 基地址（例如 `https://model.example/v1`）、模型名称和 API Key。保存后可以显式点击连接测试；测试会发起一次实际模型调用。空白密钥输入表示保留已有密钥，页面不会读回原密钥。禁用模式停止后续模型调用。

服务端调用 `/chat/completions`，拒绝 HTTP、带 URL 凭证、跳转及过大的响应。使用量和费用由所选模型服务的账号承担。普通聊天只有在用户开启模型授权后才发送必要的有效记忆、相关记录、对话和最多六件进行中的事情；不同用户的上下文保持隔离。

## 服务器 Claude / Codex 模式

“本地”指 **CLI 运行在管理员的服务器上**。Claude/Codex 通常仍连接厂商云模型，使用服务器上已登录账号的额度；它不等于离线模型，也不是在网页访问者的电脑上运行。

Python 服务可以直接调用本机安装的原生命令行程序。管理员选择本地模式和 `codex` 或 `claude`，可选填写该 CLI 支持的具体模型名称。自定义安装位置使用服务器环境 `COMPANION_CODEX_BIN` 或 `COMPANION_CLAUDE_BIN`；Windows 使用原生 `.exe`。请求不能指定任意命令、参数或工作目录。

Cloudflare Worker 无法启动系统子进程。公开 Web 的本地模式需要连接独立服务器上的网关：

1. 在专用服务器账号下安装并登录所选 CLI。使用支持官方文档中参数的版本；不兼容时明确失败，不绕过权限。本轮开发机实际验收为 Codex CLI `0.166.0-alpha.5.1`；Claude 未安装，只有适配契约测试。
2. 安装 Python 项目依赖，在服务器 `.env` 设置随机且至少32字符的 `COMPANION_CLI_GATEWAY_TOKEN`。
3. 执行 `python -m companion.cli_gateway`，默认监听 `127.0.0.1:8777`，不记录 HTTP access log。
4. 通过自己的 HTTPS 反向代理或可信私网连接提供网关。在 Web 管理员设置填入 `https://your-cli.example/v1` 和匹配令牌，做连接测试。

网关只接收固定模型枚举和有界文本消息。每次在独立临时目录执行；Claude 关闭工具/MCP/会话保存，Codex 忽略用户配置、关闭执行/应用/浏览器等能力，使用只读沙箱和临时会话。并发数限制为一，执行有超时及输出上限。运营服务器应使用独立服务账号，避免把私人开发环境当多用户执行环境。

官方参数参考：[Codex 非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode)、[Claude CLI](https://code.claude.com/docs/en/cli-reference)。

## 管理员与密钥

首位管理员由服务器持有的随机初始化令牌授予已注册的正式账号，不会把首位公开注册者自动设为管理员。Python 使用 `COMPANION_ADMIN_SETUP_TOKEN`；Worker 使用部署 secret `ADMIN_SETUP_TOKEN`，调用 `POST /api/internal/admin/grant`，正文只含目标邮箱。初始化成功后不可再次使用这个入口授权。

之后由现有管理员在管理员区域精确查找邮箱，授权或撤销管理员。演示账号不能提权；角色变化立即影响现有会话；最后一位管理员不能被撤权或删除。账号目录只提供身份与角色摘要，不提供其他用户的行为、记忆或聊天。

持久化模型配置使用 AES-GCM 加密。Python 必须设置随机的 `COMPANION_CONFIG_ENCRYPTION_KEY`（至少32字符）；Worker 可使用独立同名 secret，未设置时从现有 `AUTH_PEPPER` 派生用途隔离的加密密钥。备份时需保留对应服务器密钥；随意更换将导致已保存配置无法解密。密码、模型密钥和 CLI 登录凭证不得写入源码或发布包。

本轮不会自动把开发机作为常驻公网服务器。生产 API / 网关地址及凭证由管理员选择；未连接服务时页面显示规则模式。Windows 本机离线模式继续隔离外部服务，通过在线账户访问统一云配置。
