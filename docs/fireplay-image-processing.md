# Fireplay 图片裁图与加工 MCP

用户于2026-11-04明确要求将正式裁图MCP接入当前所有图片处理会话。共享CLI是 `scripts/fireplay-image-processing.mjs`，通过现有AuthHub代理使用AI Designer正式目录，不需要另一个三方服务或复制登录信息。

## 执行

1. 从 `work/fireplay-production/image-processing-data-20261104.json` 找本需求UUID、源附件URL、原语言字段及已绑定素材。该文件是37项需求的实时快照，执行前仍刷新本UUID。附件尺寸来自本地下载记录，是源图尺寸，最终尺寸须按需求和实图确认；quantity不自动乘语言数。
2. 按 `work/fireplay-readonly/crop-mcp-contracts-20261104.json` 选择工具。CLI会实时检查合同版本。`generation_asset_upload_url` 将真实源URL转为本人可信资产；已有素材用正式转移接口时先查其独立合同。上传回执的asset ID才是后续source_asset_ids，附件ID和material ID不可代替。
3. 先以 `confirmed:false` 调预检。每次写调用采用独立回执路径，原回执存在即停止；未知效果查询原任务，不换key重放。预检通过只证明参数通过，不证明来源权限、额度、模型实际生成或输出质量已验证。
4. `generation_crop_create` 是AI重构裁图，可能计费，仅接受square_1x1或portrait_4x5；每批最多16源×24目标，先按需求控制展开数量。当前工作区默认模型快照生效，不能在crop参数中塞model_id。需要明确费用授权后才能confirmed:true；用户本次接入授权未指定付费张数或预算。
5. `generation_translate_create` 独立做图片翻译，每批最多16组、每组16源、20语言；按已核实原文和目标语言给精确prompt。原文残缺仍待核实；参考图生成不能替代真实审核。
6. `generation_image_resize` 做确定性尺寸处理，不调用生成模型；支持1..8192宽高与fit/contain/cover/stretch，准确记录裁切、留白和拉伸。尺寸变更必须符合当前需求；按钮150KB门禁继续生效。
7. `generation_image_job_query` 读回任务与逐项结果；下载当前结果，保存哈希，按分层/源图差异标准真实审图。已生成不等于可交付。合格后沿现有publication合同入库、归属共享、绑定exact UUID并读回。历史失败与旧成品保留。

## 调用入口

使用宿主自带Node执行：

```text
node scripts/fireplay-image-processing.mjs --request work/fireplay-production/需求编号/processing-request.json --receipt work/fireplay-readonly/本次唯一操作.jsonl
```

request形态：`{"tool":"generation_crop_create","arguments":{"workspace_id":"真实本人工作区UUID","source_asset_ids":["真实本人ready资产UUID"],"targets":[{"specCode":"square_1x1","prompt":"按本源图保留指定主体及完整文字，适配方图；具体构图由当前源图确定"}],"idempotency_key":"本需求源哈希与参数固定key","confirmed":false}}`。占位示例不可直接执行。CLI支持的名字、参数以脚本和上述固定合同为准。

## 2026-11-04 实时发现

37/37需求，61条附件记录（可跨需求复用，不等于61张独立待制作源）；11按钮、26图片。27项已有绑定素材，只表示存在交付，不能推断27项全部完成。

当前可用模型：GPT Image2 KIE `kie-gpt-image-2-1k`（默认，最多16参考）、Nano Banana2 KIE `kie-nano-banana-2-1k`（最多14参考）、Qwen Image3.0 `qwen-image-3.0`（最多3参考）。三者支持image_to_image，当前均不支持蒙版。此能力与宿主text-only image_gen是两条独立链路。

本人额度查询余额999.152HDK；KIE图片估价0.2HDK/张，Qwen输出0.2016HDK/张另加每参考0.0224HDK。余额与估价均为瞬时查询，不构成费用授权或最终账单。

055已有工作区 `de7781af-0848-4da0-8334-338e4749170f`；其余需求在当前本人82个工作区返回中未发现同名项，执行时查实后按正式创建接口处理。完整模型/额度/工作区和37项原始读回：`work/fireplay-readonly/crop-mcp-live-20261104.json`。
