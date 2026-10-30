# 架构与数据合同

## 分工

宿主 AI 实际看源图、识字、逐区域翻译、提取样式、按视觉需求选择脚本/模型/设计工具并审核。CLI 在本地准备工作包、校验数据、测量基础排版、合成、生成报告和执行哈希门禁。宿主能力不等于存在可自动调用的视觉 API；当前不内置 OCR 服务或无人值守 Agent 循环。

基础 CLI 实现矩形横排 solid/clean_plate，不自动识别弧形。新增 [分层计划接口](layered-tools.md) 以独立合同实现逐行采样修复、透明文字与旋转描边、现有素材合成和保护像素/哈希检查；旧manifest/quality合同不变。五阶段语义与视觉判断、弯曲/透视设计及复杂未知纹理仍由Agent或真正编辑工具负责。方法合同见 [分层本地化](../knowledge/methods/layered-localization.md)，适用分支见 [内容路由](../knowledge/methods/content-routing.md)。

路径均按项目相对路径解释；`RUN` 指 `runs/ID`，不是系统环境变量。工作包和报告以命令回执中的真实路径为准，禁止凭模板虚构产物。源图不覆盖；候选输出与对应审核留在该 run，版本变更必须重新审核。

## manifest.json

文件位于 run 内，`schema_version=1`。初始化按EXIF方向规范化保存 `source.png`，坐标均以规范化画布为准；原文件不改写。字段合同如下，新增实现字段不得改变这些含义。

| 字段 | 类型与约束 |
| --- | --- |
| `canvas` | `{width,height}`，源图像素尺寸，正整数；不缩放画布 |
| `source` | `{path,sha256,original_sha256}`，path指向run内规范化 `source.png`；sha256绑定该PNG的文件字节，original_sha256单独绑定原始输入文件字节 |
| `target_language` | 目标语言，如 `zh-CN` |
| `regions` | 文字区域数组，含翻译与保留项；每项使用下述合同 |
| `protected_regions` | `[{id,bbox}]`，人物、商标、图标等禁止改变区域；id唯一 |
| `glossary` | `[{source,target}]`，本次定稿术语，不自动升级为长期偏好 |
| `analysis` | `{complete:false,observations:[],unresolved:[]}`，默认未完成；observations记录具体事实、证据和处理结果，unresolved非空阻断渲染 |

每个 `regions` 项：

| 字段 | 类型与约束 |
| --- | --- |
| `id` | 唯一稳定字符串，如 `r01`；返工不随排序改名 |
| `action` | `translate` 或 `preserve` |
| `source_text` | 实际辨认原文；不可辨部分明确记“无法辨认”，notes写位置及原因，不猜补 |
| `target_text` | `translate` 为经核对译文；`preserve` 与原文一致，notes说明保留依据 |
| `bbox` | `[x,y,w,h]`，源画布左上角为原点，像素整数；x/y非负，w/h为正，不越界；范围为左闭右开、上闭下开 |
| `confidence` | 0到1的识读置信度，由宿主判断，不冒称OCR概率；低于0.9列为人工复核项，高置信度也不能免审 |
| `notes` | 具体观察、保留理由、读序、疑点及解决依据；不可只写“正常” |
| `style` | `{font_path,min_font_size,max_font_size,color,align,padding,line_spacing,stroke_width,stroke_fill}` |
| `repair` | `{mode,color}`，mode只允许 `solid` / `clean_plate`；保留项不执行修复 |

样式约定：`font_path` 为可访问本地字体的绝对路径；字号为像素正数且min≤max；`color`/`stroke_fill` 使用 `#RRGGBB`；`align` 为 `left`/`center`/`right`；`padding` 为四边统一的非负像素整数；`line_spacing` 为非负额外行间距像素；`stroke_width` 为非负像素整数。纯色模式必须有经过源图取样和审核的 `repair.color`，clean_plate模式中该字段不参与修复。程序按最大可容纳字号确定性换行；最小字号仍放不下则失败，不静默裁字、压扁或无限缩小。

翻译区相互重叠、翻译区与保护区/保留区相交时停止并调整区域或路线，不能依赖绘制顺序碰运气。脚本测量通过仅说明文字框可放入，不能证明无缺字方框、字体适配、语义正确或背景自然。

`analysis.complete=true` 只在实际逐区检查、全图补漏及疑点解决后设置，`analysis.unresolved` 必须为空。`confidence<0.9` 的区域实际复核后还须 `recognition_verified=true`，notes记录依据，不能仅改标志。不得把不可辨内容改为保留来规避漏译。确实没有文字时需 `analysis.no_text_verified=true` 且observations记录实际全图检查证据；空清单不自动通过质量门禁。

## clean plate与合成

`solid` 仅适用于实际均匀底色；渐变、纹理、人物边缘或透视文字不得用随意色块冒充还原。`clean_plate` 是接收已去字本地底图的接口，来源可为可靠局部源像素重建、矢量容器重建或用户/宿主真正编辑；不能因复杂就一律限定外部编辑。底图必须全画布同尺寸，以 `render RUN --clean-background PATH` 接入，不自动缩放错位文件。CLI 只从底图拷贝 translate 矩形再绘字，保护区及区域外保持源图。尺寸相同仍须实际审看残字、接缝、主体及对齐；未知遮挡纹理不能宣称逐像素恢复。

渲染报告必须记录实际源图/manifest/字体/clean plate（如使用）/输出路径与SHA256，字体使用 `font_sha256`，并记录排版字号和警告。底图或字体变更必须重新渲染；不能复用旧审核。当前文字prompt生成工具不具备原图/蒙版输入，不作为精准修图实现；可按当前用户授权做设计参考，必须核查文字，次数/费用按当前授权，未知授权不自动调用付费工具。缺可靠底图时 pending，说明区域和能力缺口。

增强路线额外保存样式计划、背景单独审核、独立透明字层、合成与全图适配记录；字段可以使用手工阶段文件，不把尚未实现的字段塞进 manifest 合同。每阶段记录实际文件/哈希、查看尺度、区域、具体观察与问题。基础 render 同时修背景/排字，其输出的透明层与 quality 报告不能自动证明独立阶段已审。

## review.json与质量门禁

审核文件位于 `runs/ID/renders/<version>/review.json`，准确路径取 `review-packet` 回执，必须由实际看过最新成图的审核者填写：

- `manifest_sha256`、`output_sha256`：对应实际文件字节SHA256。
- `reviewer`：真实审核者标识；`review_kind`：同一Agent自审填 `self`，另一个实际独立审核者填 `independent`。
- `checks`：每个region恰好一项 `{id,semantic,completeness,legibility,restoration,style,notes}`；五个检查字段均为bool，notes写具体观察。preserve项也检查原意、文字、视觉是否保留，禁止漏项。
- `canvas_preserved`、`protected_content_preserved`：全局bool，检查画布尺寸及非目标内容。
- `issues`：`[{severity,region_id,note}]`，severity为 `major`/`minor`/`critical`；全局问题region_id用 `null`，note必须明确位置、现象与影响。
- `global_notes`：必填，写全图与保护内容的实际观察；存在数字/术语等提示时，实际核对后填写 `advisories_reviewed=true`，不能只勾选跳过核对。

未看图就没有真实审核；模板可留空，但不得预填true或伪造reviewer。缺项保持pending，明确看到的失败填false并记问题。minor仅指不影响语义、完整性、可读性和还原的轻微外观差异；硬性检查失败不能降级minor。

`quality` 应分别报告技术验证和审核门禁：缺真实审核/能力/证据为pending；哈希不符、字段非法或已确认缺陷阻断通过；pass要求解析完整、技术有效、哈希绑定一致、逐区五项及全局两项均true、无major/critical、无未解决疑点，minor如实披露。报告不能仅凭存在JSON就称审核真实，需保留实际对照观察。技术完成、Agent质量通过和用户验收分别记录，脚本不得自动代表用户验收。

## 知识与样式计划

[索引](../knowledge/index.json) 合同为 `{version:1,entries:[{id,path,kind,status,tags,scope}]}`；id唯一，path为项目相对 Markdown 路径，kind 为 method/case，status 为 active/provisional/partial。案例可附 `source_sha256` 供精确源图关联。通用授权流程 active，少样例技术方法 provisional，范围有限或有已披露取舍的案例可保留 partial；用户反馈另行绑定具体版本。

packet 输出知识入口、索引和通用方法正文；案例依据 source_sha256 或 manifest.analysis.content_tags 与 entry.tags 的相关性载入。内容标签由 Agent 的实际观察填写，不代表脚本已识别图片。工作包提供样式计划/阶段说明入口，阶段文件的存在不证明已执行或审核；当前增强路线仍由 Agent 手工完成。按 [文字构图方法](../knowledge/methods/typography-composition.md) 记录字重/黑白面积、宽高比、字距、立体感、弧度/透视、光照、张力与容器关系，不能只记字号相同。

历史run与审核不删除，不自动将旧pass升级成满足新视觉要求的pass。用户认可相对改进与完整验收分开记录；当前 [缎带案例](../knowledge/cases/ribbon-20261029/README.md) 为partial。

## CLI接口

| 命令 | 责任与完成证据 |
| --- | --- |
| `python bin/it.py init SOURCE --target zh-CN --run runs/ID` | 建立单图run、源哈希和未完成manifest；不自动识字 |
| `python bin/it.py packet RUN` | 输出源图、数据、知识及待办工作包；宿主实际打开图片 |
| `python bin/it.py validate RUN` | 验证字段、尺寸、区域、路径、哈希、样式与冲突；不替代视觉判断 |
| `python bin/it.py render RUN [--clean-background PATH]` | 本地确定性修复合成、排版及技术报告；缺所需底图则阻断 |
| `python bin/it.py review-packet RUN` | 生成源/成图对照与局部证据入口，不自动作审核结论 |
| `python bin/it.py quality RUN` | 检查技术、审核覆盖及哈希，输出pass/fail/pending与原因 |
| `python bin/it.py demo --output work/demo` | 本地可重复的示例验证；程序结果不冒称真实AI审核通过 |
| `python bin/it.py prepare-layered PLAN --output DIR` | 配置生成无字背景/蒙版与技术回执，实际审背景后再继续 |
| `python bin/it.py render-layered PLAN --background DIR --output NEW_DIR` | 复用背景，生成独立字层/素材层、合成与检查证据；不自动完成审核 |
| `python bin/it.py check-layered DIR` | 独立分层门禁，0通过/2待审/1失败，合同见 [分层工具](layered-tools.md) |
| `python bin/it.py doctor` | 报本地运行环境、字体和能力状态；凭证presence不等于服务verified |

实现若尚缺某命令/字段，报告实际缺口；不得用文档声明代替运行证据。
