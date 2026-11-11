# DSH 图片审阅插件

> 已退役：当前`client.js`为空入口，以下为原实现的历史设计记录，不是当前安装合同。现用包为[流程台](../image-review-workbench/README.md)；本目录保留Host适配回归，不再按本页安装。

这是本项目的工作区 bundle，不是已经安装的声明。包使用当前安装附带的官方 lazy Client module 格式和 `dsh.bundle.patch`；无需构建、下载依赖或安装另一份 React。

## 注册与隔离

- 包名：`@local/image-review-desk`；唯一插件行和面板键：`local-image-review-desk`。
- 侧栏贡献 `sidebar.panellist` 的图标；主面板贡献同键 `main`。
- 读取官方 `sessions.list` 中 `retainedBy.mainView` 标记的当前会话，保留进入面板前的当前会话身份。
- 绑定目录为 `C:/Users/Echo/Desktop/单图片翻译`。Windows 路径仅做分隔符、尾斜杠、大小写规范化，再做完整等值比较。不按目录前缀放宽，不扫描会话日志。
- 当前会话不在此目录，主面板不嵌入项目页面，也不派发；关联会话下拉框仅包含同一目录、非子 Agent 的会话。每次发送前重新核对当前会话和目标会话。
- 安装是 profile 级；项目数据和派发目标由上述项目边界限制。

## 本地服务

Host 入口不启动 Python。需主项目先启动 `review-desk --dsh-origin http://127.0.0.1:64626`，以允许这一精确 DSH origin 嵌入。iframe 固定为 `http://127.0.0.1:8765/`，保留精确来源和 referrer。

未收到有效任务消息会报告服务未就绪/缺少桥接。不能由该提示推断服务已经自动启动。独立审阅台仍可按项目入口使用。

## 返工交接

1. 用户选择同项目的会话并勾选确认，界面显示其完整 ID。
2. 在 iframe 保存实际审阅意见。
3. 点击“读取已保存的返工意见”，请求子页面从服务取得指令；此时不发会话消息。
4. 检查完整指令预览，再点击“发送返工到关联会话”。发送前重新读取已保存意见并核对任务、feedback_id、三哈希与完整 prompt。变化则取消。
5. 官方 `ctx.remote.session.prompt` 以 `mode:'queue'` 提交文本，只有 `ok:true` 且 `value.accepted:true` 才报告会话已接收。接收不等于执行成功；失败保留预览，支持复制。未知结果重试复用同一 requestId，避免同一点击意图反复生成消息。

## Bridge v1

所有消息均验证 `event.origin === 'http://127.0.0.1:8765'` 且 `event.source === iframe.contentWindow`。只响应本面板生成且未超时的 requestId；卸载移除监听器、清理定时器并拒绝在途请求。子页面消息永远不会直接调用会话 API。

Parent → child：

```js
{type:'image-review-host-context',version:1,sessionId,sessionTitle,projectRoot}
{type:'image-review-request-revision',version:1,requestId}
```

Child → parent：

```js
{type:'image-review-task',version:1,project_id,taskId,source_sha256,output_sha256,manifest_sha256}
{type:'image-review-revision',version:1,requestId,project_id,sessionId,prompt,task_id,feedback_id,
 actionable,decision,source_sha256,output_sha256,manifest_sha256}
// 失败：
{type:'image-review-revision-error',version:1,requestId,error}
```

哈希必须为64位hex；feedback_id为非空string，actionable必须为true，否则拒绝预览和发送。project_id必须与此目录按服务端project_identity计算的固定值一致；revision的sessionId必须回显本轮确认的目标会话。重复发布同一task/三哈希不会清空预览或重置失败重试requestId。未成图或任务不可返工时task消息可无有效三哈希，父面板禁用返工。prompt必须来自当前已保存反馈，子页面及服务负责现读三哈希和质量合同；父面板验证响应与当前任务身份相等，不接受子页面变更关联目标，不传会话凭证或审阅API令牌。

## 安装和验收

主 Agent 使用官方 `plugin_manager(action='install_bundle', target=本包绝对目录)`。不手改 profile、不在profile运行包管理器、不从其他会话取令牌。本轮只交付可验证的包；若当前会话没有安装工具，需通过已授权的正式入口安装或在具备该工具的 Creator 会话继续。

`node scripts/plugin-contract-check.mjs` 覆盖manifest/资源、lazy factory注册、项目路径、task和revision校验、来源/source伪造、未请求回复、request/response和卸载清理。它不能证明已安装或真实会话发送成功。正式安装后应在 DSH 验证侧栏、iframe嵌入、跨项目隐藏、同项目会话确认、成功与失败提示及卸载。

## 已核实的官方来源

本项目 `.review-desk/sdk-reference/dsh/node_modules/@deepseek-ai/` 中：

- `dsh-agent-preset/skills/cordis-plugin-development/templates/decoration/*`：manifest、Host和lazy module格式。
- `dsh-client-ui-layout/lib/client.js`：根级 keyed main，key随activePanelId选择。
- `dsh-client-ui-sidebar/lib/client.js`：panellist的id、label、图标props(size/active)。
- `dsh-api-session-controller/lib/types/client/sessions/service.js`：list.byId行字段和retainedBy。
- `dsh-api-session-controller/lib/typert.remote-client.js`及`types/client/sessions/session.js`：remote.session.prompt参数和RemoteResult。

原创SVG及双语meta均随包分发。界面使用宿主主题变量、宿主React，未复制其他插件组件样式。
