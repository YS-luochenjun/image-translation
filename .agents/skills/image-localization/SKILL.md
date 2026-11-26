---
name: image-localization
description: 在本项目按图片翻译需求自动接入长方竖图和大按钮、规划成员、制作评分并通过官方连接器提交；本地单图按内容选路。
---

# 项目执行入口

先读 [AGENTS](../../../AGENTS.md)。需求面板接单、批量交付或续作时按需求发现、成员规划、制作评分、官方 MCP 上传入库与绑定、回执结案推进。只有异常项需要补核，见[自主交付流程](../../../agent/workflows/fireplay-autonomous.md)。本地单图读[单图工作流](../../../agent/workflows/single-image.md)。

需求面板批次按[会话分工与调度](../../../agent/workflows/fireplay-autonomous.md#会话分工与调度)核对负责人并登记派发。原执行会话用 `delivery-next` 核齐官方上传、入库及绑定回执后，必须亲自写回「已完成」，一次面板读回确认状态和素材，保存 `status-finish.json` 的 `confirmed` 回执后才能报告结案。续办的旧需求也按此规则执行。缺件或写回受阻时，记录实际缺口和下一步。具体步骤见[运行手册](../../../docs/optimization-pilot.md#fireplay-从开工到完成)。

所有制图与审核先应用[交付评分](../../../knowledge/methods/delivery-balanced-review.md)：默认画面流畅、基本一致，允许局部差异和补全；正常展示不显眼且不触及硬项的轻微修图痕迹记minor，不触发返工。≥80且底线通过即交付；未达标且有可行路线才继续局部返工，确有阻塞时只挂起关联成员。按[图组与尺寸规划](../../../knowledge/methods/source-set-and-size-planning.md)识别真实来源组，按实际成员逐套翻译和提交。独立原图需要派生版式且缺明确规格时默认七张，大按钮走自身规格。

按需查阅 [知识目录](../../../knowledge/README.md) 与 [索引](../../../knowledge/index.json)：通用阶段读 [分层流程](../../../knowledge/methods/layered-localization.md)，选技术路线读 [内容路由](../../../knowledge/methods/content-routing.md)，文字视觉设计读 [文字构图](../../../knowledge/methods/typography-composition.md)。仅在当前图条件相符时读取相关案例，并核查状态、证据和最新反馈。

具体字段与门禁以 [架构合同](../../../docs/architecture.md)、[配置分层工具](../../../docs/layered-tools.md)、[原生观感任务入口](../../../docs/native-workflow.md) 和实际命令回执为准。新分阶段任务优先使用任务入口，语义与实际字层逐区绑定，背景真实审核后才排字。已有操作优先写每图配置；只有表达不了的特殊需求才加专用实现。解析、翻译、修复、审核分别使用工作流链接的提示词。本入口不复制知识正文，不写全局 skill 或 hook。
