---
name: image-localization
description: 在本项目按图片翻译需求自动接入长方竖图和大按钮、规划成员、制作评分并通过官方连接器提交；本地单图按内容选路。
---

# 项目执行入口

先读 [AGENTS](../../../AGENTS.md)。需求面板接单、批量交付或续作时读[自主交付流程](../../../agent/workflows/fireplay-autonomous.md)，沿需求发现→明确成员矩阵→制作→评分→上传入库归属共享绑定→fresh终核连续执行，已授权步骤不逐次求许可；本地单图读[单图工作流](../../../agent/workflows/single-image.md)。按实际工具能力执行，不把知识文档视为自动实现。

需求面板批次的主控与执行会话遵守自主交付流程中的[会话分工与调度](../../../agent/workflows/fireplay-autonomous.md#会话分工与调度)，接入时先核现有会话并登记派发状态。

所有制图与审核先应用[交付评分](../../../knowledge/methods/delivery-balanced-review.md)：默认画面流畅、基本一致，允许局部差异和补全；≥80且底线通过即交付，不追完美。初稿后最多两轮局部返工，未通过只挂起关联成员，继续其他可执行项；原图完整集合不盲乘七，大按钮走自身规格；整组来源、跨语言成员覆盖或尺寸取舍时读[图组与尺寸规划](../../../knowledge/methods/source-set-and-size-planning.md)。

按需查阅 [知识目录](../../../knowledge/README.md) 与 [索引](../../../knowledge/index.json)：通用阶段读 [分层流程](../../../knowledge/methods/layered-localization.md)，选技术路线读 [内容路由](../../../knowledge/methods/content-routing.md)，文字视觉设计读 [文字构图](../../../knowledge/methods/typography-composition.md)。仅在当前图条件相符时读取相关案例，并核查状态、证据和最新反馈。

具体字段与门禁以 [架构合同](../../../docs/architecture.md)、[配置分层工具](../../../docs/layered-tools.md)、[原生观感任务入口](../../../docs/native-workflow.md) 和实际命令回执为准。新分阶段任务优先使用任务入口，语义与实际字层逐区绑定，背景真实审核后才排字。已有操作优先写每图配置；只有表达不了的特殊需求才加专用实现。解析、翻译、修复、审核分别使用工作流链接的提示词。本入口不复制知识正文，不写全局 skill 或 hook。
