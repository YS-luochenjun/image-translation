# 插件目录

源码收录不等于当前宿主已安装或已激活。安装只走宿主正式管理器，迁移前核对项目路径、项目指纹和解释器路径；不复制凭证或 profile。

| 目录 | 定位 | 使用边界 |
| --- | --- | --- |
| `image-review-workbench/` | 当前流程台源码，包 `@local/image-review-ui@5.2.1` | 八组制作步骤、同源图片及显式会话控制；见[流程台](../docs/workflow-desk.md)。本次只做离线回归，不重新安装或声明当前GUI已验收 |
| `image-review-desk/` | 退役审阅台及历史适配测试 | `client.js` 已是空入口；保留回归和演进证据，不作为安装入口 |
| `fireplay-session-dispatch/` | 2026-11-03固定37需求的专用调度器 | 冻结清单、原会话身份、最多四槽位、结果未知不重放；依赖本地历史控制和审核记录，不能用于未来新需求 |
| `fireplay-requirements-mcp/` | 共享AuthHub代理及本项目连接示例 | 当前 `.mcp.json` 和 `dsh-entry.yaml` 仅配置 AI Designer；代理可连接指定服务，其他服务需各自配置。凭证存放在仓库外 |

工作流与技术模块的边界：

- 通用需求主控/执行会话分工以[自主交付流程](../agent/workflows/fireplay-autonomous.md)为准。
- 固定37清单的专门授权和恢复规则只约束旧批次；测试夹具不授予安装、创建会话或生产写入权限。
- Python `src/image_localizer/` 负责本地工作包和门禁，`scripts/fireplay-mcp-publication.mjs` 负责受约束发布；UI的“已接收”不替代质量或交付证据。
- 现用插件仍有此机器的绝对路径绑定。跨机器安装、当前宿主SDK兼容和真实发送/停止属于后续实机验收范围。
