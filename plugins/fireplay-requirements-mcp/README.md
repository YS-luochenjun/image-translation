# Fireplay MCP Suite

一次 AuthHub / 飞书登录，连接 9 个公司业务 MCP。此包复用现有 Fireplay MCP Suite 的 Node.js 登录代理，不携带任何人的凭证，也不复制 OA 的系统 skills。

本项目收录的`.mcp.json`和`dsh-entry.yaml`只配置AI Designer。下表是代理的服务适用范围，不代表此目录已经注册全部9个连接；其他服务需要正式配置并核验。两份配置含本机绝对路径，迁移时按目标环境调整。

## 服务范围

| 服务名 | 用途 |
|---|---|
| campaigns | Fireplay 策略、任务、投放执行与恢复 |
| site | 站点、落地页、匹配规则与自动内容任务 |
| ai-designer | 素材库、需求、订单和图片制作；使用 compact catalog 入口 |
| analysis | 广告历史效果、维度指标与实体分析 |
| jarvis-mcp | Jarvis 调控助理；本工作区仅使用只读查询 |
| google_analytics | GA4 属性发现与报表 |
| adradar | 竞品广告和创意检索 |
| studio | 广告看板、CMS3 效果与平台调控能力，依账号和服务注册状态开放 |
| logging | 跨服务日志、执行过程与关联链排查 |

这里不包含 Penny、代码审核或第三方通用插件；这些能力需独立连接。注册了服务不代表当前账号有全部资源权限，也不证明每个服务当前在线。

## 登录与运行

需要 Node.js 20+，无需 npm install 或其他第三方 Node 依赖。代理通过标准输入输出对接 Codex，连接远程 MCP，并共享一份本机 AuthHub 会话。

首次登录的回调为 `http://localhost:10086/auth/callback`，必须能使用该本地端口，且地址已在公司登录服务中获准。登录等待上限为 5 分钟；若宿主的 MCP 启动等待更短，先完成浏览器登录，再重新连接服务。

Windows 凭证使用当前用户 DPAPI 保护，存放在 `%LOCALAPPDATA%/Tanyue/FireplayMcpSuite`。未设置 LOCALAPPDATA 的 macOS / Linux 用户使用 `~/.fireplay-mcp-suite/auth.json`，以仅当前用户可读写的文件权限保存。macOS / Linux 这一实现不使用系统钥匙串加密。凭证不在插件目录内，切勿把凭证目录加入分发包。

代理支持令牌到期前刷新、跨进程共享登录锁与刷新锁、上游 401 后刷新并重试一次；网络超时不自动重放业务请求。AuthHub 请求默认 60 秒、MCP 请求默认 120 秒。HTTP 连接地址沿用现有公司配置，其中 Analysis、Jarvis 和 GA 仍使用 HTTP；分发包未改变其传输部署。

可选环境变量：

| 变量 | 默认值 / 用途 |
|---|---|
| FIREPLAY_AUTHHUB_URL | `https://authhub.cms1.cc/api/v1` |
| FIREPLAY_AUTH_CALLBACK_URL | `http://localhost:10086/auth/callback` |
| FIREPLAY_AUTHHUB_TIMEOUT_MS | `60000` |
| FIREPLAY_MCP_REQUEST_TIMEOUT_MS | `120000` |

## 使用边界

服务端按当前登录用户做资源授权。安装插件不构成真实投放、调预算、收费图片生成、通知或其他业务写入的授权；执行前应确认用户授权的对象、参数及范围。Jarvis 在本工作区继续仅用于只读查询。

上述操作边界是使用约定，本插件未新增工具级拦截器；实际工具是否注册、账号权限和服务端开关以调用时返回为准。需要团队流程与系统操作知识时，继续使用 OA 的 `systems/` 正本及其生成 skills。

业务请求超时或结果未知时，先沿原任务 ID 核实状态，避免重复创建。日志用于解释过程，当前业务终态仍向所属业务系统查询。任务创建、执行成功、已投放、产生消耗是不同状态。

## 本版变更

相对于原 7 服务分发包，增加 Studio、Logging，并将 AI Designer 连接切换到当前已配置使用的 `/api/mcp/catalog/v1`。未改变远程服务实现，未安装、替换或发布任何插件。
