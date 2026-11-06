# Fireplay 图片翻译需求接入

本流程负责回执驱动接入和下载；需求面板自主发现到正式提交先读[自主交付流程](fireplay-autonomous.md)。宿主Agent查询Fireplay，Python保存需求快照、登记本地承接、下载验证图片并建立多语工作包。平台字段和权限以当前连接器合同为准。

## 执行顺序

1. 新接单用 `node scripts/fireplay-discover-translations.mjs --department DEPT_ID` 在线读当前选项和全部summary游标页，精确筛选图片翻译模式及image/button；保留实际query/summary_pages后交给 `python bin/it.py fireplay-discover --receipt FILE`。仅准备手工回执时可先跑无参数fireplay-discover；它返回reference_only，不证明在线接口缺失。AI模式选项无匹配或连接器缺失报告具体原因，不靠标题或空列表猜。
2. 将实际查询参数及原响应保存为 `summary_pages`，执行 `fireplay-discover --receipt FILE`。`complete=false` 时继续相同过滤与游标；完整空列表才是本次范围的空结果。P0 排在前面。列表标题仅提供线索，所有候选仍需读正文。
3. 用精确编号选中真实UUID，读取blind_brief。两视图编号、UUID、updatedAt和assetType（image或button）必须一致；分别保存summary与brief再执行fireplay-register。精确aiMode符合只证明分流，正文仍应明确翻译/尺寸适配及实际来源；原创设计或标题含翻译不能自动承接。有来源decision可处理正文与结构化字段差异，不能以任意文件自称用户授权。
4. 核对每张原图和全部目标语种。显式来源需真实正文或 `material_receipt` 中可核实的 URL；扩展名不明确的素材通过当前素材详情工具解析，不猜素材 ID。语言缺失或不支持、来源缺失、已完成或取消都保留 blocker。正文与结构化语言冲突时先核实，再用有来源的 decision 选定；注册不会推断数量的业务含义。
5. 在用户交办范围内执行 `fireplay-accept CODE`。这是本地承接，`platform_claimed=false`；平台原子认领、执行人和状态回写仍由未来平台接口提供，不能用设计订单 accept 代替需求认领。多人同时制作的风险需从平台当前执行信息核对。
6. 对每张来源执行 `fireplay-download CODE --image N`。核验真实静态 PNG/JPEG/WebP、文件大小、尺寸和 SHA256；链接失效时刷新真实素材回执。下载失败保持失败，不拿 HTML/缩略图/视频代替原图。
7. 执行 `fireplay-job CODE --image N --output runs/CODE-N`，复用已验证原图，创建所有已解析目标语言的 pending 工作包。按 [单图流程](single-image.md) 实际看图、定稿、制作和审核。多张原图分别建立工作包，完成一张不代表整个需求完成。

## 续作

`fireplay-status CODE` 读取本地状态。相同需求内容重复注册保留承接与下载；正文、原图、语言或数量变化保留历史版本，新版本进入 `revision_pending`，核实后重新本地承接。状态、优先级、updatedAt 单独变化不会生成内容版本，但关闭状态仍阻止继续下载。已有原图重用前重新检查哈希，发现变化即报错，不覆盖旧源。

本地 `state/fireplay/CODE/` 保存当前 request、版本、不可变内容/回执/承接快照和原图。并发状态写入使用排他锁；残留锁先核实原操作，再决定恢复，不盲删。`fireplay-source.json` 仅证明工作包来源，不是视觉审核。

## 当前可验证范围

2026-11-06：已有官方代理、端到端发布journal与真实交付记录；新在线发现命令可解析官方图片翻译选项、查询image/button并分页。本地接入CLI仍不直接发MCP请求，不把reference_only解释为宿主不能上传。历史2026-11-03的原始接入范围见[当时核查报告](../../work/fireplay-readonly/findings-2026-11-03.md)，后续能力以当前发布脚本、合同和逐资源正式回执为准。

平台状态修改、认领和收费各按真实工具与当前交办范围；素材提交不等于需求状态done。参考项目凭证、个案付费授权和视频容差均不继承。用户已交办需求的常规制作、合格minor接受和上传阶段不重复问同一许可。

输入格式和操作示例见 [接入使用说明](../../docs/fireplay-intake.md)。
