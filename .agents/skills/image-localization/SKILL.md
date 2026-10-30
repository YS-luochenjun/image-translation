---
name: image-localization
description: 在本项目内执行本地单张图片翻译，按内容选路并保留分层制作和真实视觉审核证据。
---

# 项目执行入口

先读 [AGENTS](../../../AGENTS.md) 和 [单图工作流](../../../agent/workflows/single-image.md)，按实际工具能力执行，不把知识文档视为自动实现。

按需查阅 [知识目录](../../../knowledge/README.md) 与 [索引](../../../knowledge/index.json)：通用阶段读 [分层流程](../../../knowledge/methods/layered-localization.md)，选技术路线读 [内容路由](../../../knowledge/methods/content-routing.md)，文字视觉设计读 [文字构图](../../../knowledge/methods/typography-composition.md)。仅在当前图条件相符时读取相关案例，并核查状态、证据和最新反馈。

具体字段与门禁以 [架构合同](../../../docs/architecture.md)、[配置分层工具](../../../docs/layered-tools.md) 和实际命令回执为准。已有操作优先写每图配置；只有表达不了的特殊需求才加专用实现。解析、翻译、修复、审核分别使用工作流链接的提示词。本入口不复制知识正文，不写全局 skill 或 hook。
