# Fireplay 图片翻译自主交付

宿主Agent负责理解、看图、翻译、选路、调用连接器与真实审核；本地CLI负责矩阵、版本、评分和文件校验，不能离开宿主自行视觉生产。用户已明确授权已交办图片需求使用付费内容与工具，包括 KIE 或 GPT ImageGen；按实际工具合同及宿主审批执行并记录费用。

## 会话分工与调度

按用户2026-11-10确认，需求面板批次每个逻辑需求对应一个独立执行会话，会话名称只用需求编号。用户已授权本项目按需求创建会话；后续交办的新编号沿用该授权。创建前核查现有会话和主控记录：同编号续用原会话；不同编号经核实为同一需求时记录重复关系并交接给唯一执行会话。

主控负责查询和接入、冻结来源与语言范围、创建及派发会话、并发排队、进度跟踪、具体纠正和交付终核。执行会话负责该需求全部来源与语言的实际制作、视觉审核、合格成果上传入库绑定及fresh核验。执行会话只处理所分配需求，按本次授权连续完成，不另行创建需求执行会话。

优化版最多同时承接 20 个逻辑需求，按 exact requirement UUID 计数。先用 `fireplay-accept CODE` 拿名额，`queued_capacity` 表示继续排队。取得名额且 Fireplay 开工读回后，建立或续用执行会话，登记 thread_id、来源×语言矩阵、开工状态和等待游标。新会话使用宿主默认模型和推理等级。主控使用 compact wait_threads 及保存的游标跟踪完成情况。创建失败时保留待派发状态。取得会话 ID 及启动回执后登记派发成功。

## 1. 发现待办，不靠标题猜

执行只读 `node scripts/fireplay-discover-translations.mjs --department DEPT_ID`。DEPT_ID用当前授权组查询结果，不能凭名称猜。跨部门轮询显式追加 `--all-departments`：该部门仅用于读取官方选项，summary查询不带dept_id；命令核对回执未被隐式缩窄。省略该参数仍为指定部门范围，scope=all本身不消除部门过滤。

命令先读当前tool_catalog和requirement_create_options，从 `fixed.aiModes` 精确匹配 enabled=true、label=图片翻译，取得value；2026-11-06官方SHE选项核实为 `image_translate_or_resize`。再以 `view=summary,scope=all,asset_type=[image,button],ai_mode=实际值,pagination=cursor,offset=0` 全部翻页；部门模式另带dept_id。maxPages耗尽返回partial及next_cursor；先交给Python fireplay-discover验证该次回执，其host_actions给出保持相同过滤的续页参数；收集续页到原summary_pages再次验证，不能把局部页重新从头计为完整。缺连接器/权限报告不可用，不报“没有需求”。status不硬编码未知本地枚举，done/cancelled/canceled不作为新生产候选，其余读正文核实。

返回的summary_pages可交给 `fireplay-discover --receipt FILE` 验证。线上查询是live keyset，不是冻结快照；重复ID/编号或翻页漂移停止当前扫描，不能擅自丢弃。AI模式为空的旧任务只有用户明确编号范围/有来源分类决策才能补入；不把标题含翻译或历史旧37固定清单当自动发现范围。

每个候选精确UUID读blind_brief，并读当前delivery（必要时素材详情），核对updatedAt、正文、原图、语言、assetType和执行权限。盲选接口waiting_selection返回的空materials不是无素材，遵守其实际选择流程。当前assigneeIds是设计执行人，materials.designerIds是已存归属，uploader/launcher不是设计师。部门共享ID与需求deptId可能不同，通过官方可用共享部门资源核实，不字符串替换。发布前fresh核实权限与身份，不把历史intake当永远有效。

日常用 `new` 查询当前官方「未开始」的图片翻译项，每24小时用 `full` 跨状态对账。已接入的三天内未完成需求按 exact UUID 跟踪，空账本跳过聊天查询。新单先确认「进行中」再派发。具体见[主控计划](fireplay-monitor.md)和[优化版运行手册](../../docs/optimization-pilot.md)。

## 2. 规格先冻结成成员矩阵

先读[图组与尺寸规划](../../knowledge/methods/source-set-and-size-planning.md)区分已有整套逐图翻译与明确成员扩图；逐语言核来源齐全，不能只核总文件数。

| 实际规格 | 计划 |
| --- | --- |
| 大按钮 button | 明确尺寸/已接受native优先；其次同组同类已交付证据；缺省横/方源300×250与336×280，竖源300×600。PNG/JPEG≤150KiB，按官方白名单，不乘七 |
| 长方竖 image，无明确特殊交付范围 | 默认`standard_seven_set`：每个独立创意×该来源去重语言各7张，1200×628×1、1200²×3、960×1200×3；无需正文再写标准输出 |
| 已提供真实1长3方3竖七成员 | `provided_source_set`：整组按语言翻译一次，不逐成员再乘七；多张横图不算完整套 |
| 用户明确只翻所供原图或特殊成员 | 记录明确依据，`provided_set_scope=explicit_source_only`或显式成员/尺寸；不能将主控自己的按附件数交付判断作为用户例外 |
| 正文写具体尺寸/数量或已有接受规格 | 逐个member列出，覆盖默认；quantity字段不是自动图片数或创意数，不能盲乘 |

运行 `material-size-plan SPEC.json`；来源id、源文件SHA、逐来源去重语言、七个member_id、尺寸及范围依据必须对应。标准七成员支持同需求多个独立来源；029的9个独立横图×意语为63张，049的4创意×7语言为196张。已有完整七成员按实际图组复用；部分成员只能报partial并继续补齐。派发正文必须写明来源分组、每来源语言、每套1长3方3竖及总成员数；回收完成结果时逐套核7张，不能凭1、2、3张已上传就结案。`reference_seven_set`仍需真实七参考resource与证据，不伪造resource_id。

业务语言按用户提供的完整[语言映射](../../src/image_localizer/languages.py)规范化，保留原值证据，目标去重后制作。其中in→hi（印地语），id/ind→id（印尼语），kr→ko；业务别名以该表为准，不能套用通用标准把in解释为印尼语。未知/错拼如ejn、miss、翻译剩余语言不能猜，回看正文或一次询问。已交付合格SHA复用，缺项才制作。

## 3. 生产与交付取舍

制作前读[交付评分](../../knowledge/methods/delivery-balanced-review.md)。默认大致一致、画面流畅、语义正确，不要求像素复刻；用户明确精确复刻时才提高对应项要求。按[单图工作流](single-image.md)看源图识字→定稿→选择本地分层/真实编辑能力→导出→逐成员看图评分。普通背景可近似补全、装饰可简化、可合理裁剪重排；品牌、产品和人物身份、动作表达、金额条件不任意改变。母版/共用底图审核一次，同SHA复用，不为每语重建。

对明确七成员任务，建议大横母版2400×1256起步；交付小横图不能直接作为足够高的方竖母版。方竖通过独立层重排保全文案主体，不重复贴整广告凑张数。按钮独立精简布局，不把长方竖裁片硬塞尺寸。

有可行改进路线时持续进行有假设的局部返工，不设固定次数；总分≥80且底线通过即提交，minor无需用户再批准。不追完美、不因修复纹理不同而整个需求停摆。未达标仅隔离关联成员/源组，其他独立合格项继续；明确列缺项，不把部分交付称完成。

## 4. 发布、归属与fresh终核

使用当前官方Fireplay MCP及项目 `scripts/fireplay-mcp-publication.mjs` 的once-only journal流程：实际文件SHA+视觉审核→R2→素材入库→二创归属/共享→绑定exact requirement UUID→当前delivery/素材资源读回→逐资源下载SHA/尺寸/字节复核。所有阶段保存真实回执，逐成员记录material_id/resource_id/URL及阶段。

用户2026-11-19明确知情同意已交办图片需求经 Fireplay 官方接口上传，即使生成公网可访问图片地址，并授权素材入库、共享、需求面板绑定或替换及核验。常规制作及交付连续执行，不因项目自行增设逐次确认停下；R2中存在重复对象本身可接受，以需求面板正确交付为目标。此指示不改变宿主自动审批与官方工具的实际限制：若操作被拒绝或工具不允许自动重试未知写入，记录具体原因并按实际要求处理，不能将本项目文字当成审批通过。

1. `material-size-validate` 核已输出矩阵及技术缺项。`delivery-score`每成员ready才进上传；`delivery-next` 汇总make/review/rework/upload/import/attribute/bind/verify；`delivery-gate STATE.json`从当前已审合格成员生成兼容旧发布器的gate JSON（保存到需求目录后交给gate_file），缺成员时requirement_complete仍false。字段与示例见[自动交付合同](../../docs/delivery-automation.md)。不能靠文件数或绑定数宣布完成。
2. 原始发布器要求已有intake、当前合同schema版本、gate_file、原操作UUID；设计师按当前用户规则排除罗晨钧和邹彬。旧37原会话归档/unknown限制由该批次控制合同承担，不是普通发现入口的候选白名单；新需求无需等待旧会话。不要为通用自动化绕过原journal或默改归属。
   合同先从当前官方`tool_catalog`读取并审查，保存完整定义到该需求目录的`contract-工具名.json`；发布器优先使用该工具的需求级快照，再与live合同逐项比对。没有需求级文件时兼容旧全局缓存；已有文件损坏、版本不符或live漂移须刷新审查，不能静默回退或复制修改发布器绕过检查。journal同时记录实际快照路径与SHA。上传回执兼容`publicUrl/resourceId`及`public_url/resource_id`，相同别名去重，值冲突或失败回执不授权入库；同需求已审SHA来源校验及unknown不重放保持有效。
3. 图组可共用一个 material 的多个 resource，button 按官方单 URL 规则。已上传或已入库成员沿原资源或素材 ID 补登记与绑定。未入库的新成员可加入组图。
   上传Tag按[素材Tag约定](../../docs/material-tags.md)：带上当前素材对应的目标语种，需求用`ja`、`hi`等就沿用，不强制中文名称；明确要求的实际尺寸建议标注，再按需要加一两个产品/主题词及需求编号，主要便于搜索。单语素材不贴整单所有语种，单尺寸素材不贴未包含的其他尺寸。已有合理标签保留，缺项可通过官方标签追加方式补齐并读回，不增设严格标签门禁。
4. `designer_ids` 排除罗晨钧（c74b62d9）和邹彬（b2fbcgab），可以留空。共享部门使用官方有效 ID。素材绑定和状态写回分别检查 `canBindMaterials` 与 `canUpdateStatus`。状态操作按[优化版运行手册](../../docs/optimization-pilot.md#fireplay-从开工到完成)执行。
5. unknown超时保留原操作ID并对账。默认阻断该需求的后续写入。经明确交办继续独立成员时，可设置 `isolate_unknown_uploads`。仅限能绑定需求内单个路径和SHA的未知R2上传，发布器核对待上传文件、入库资源或绑定素材的来源不与未知项重叠。未知入库、绑定或其他未知写入仍阻断该需求。
6. fresh终核必须记录当前exact UUID绑定、设计师（可为空）、二创、共享、真实资源全部覆盖及每个下载SHA；缺证据=verify_pending，评分达标不代表delivery_verified。

## 5. 自主续作

有名额时按主控计划补入排队需求。已占位需求沿原会话继续，要求的原文或保护素材缺失时一次集中核对。已交办图片需求可依用户授权使用付费路线。已确认的语义编辑绑定当前范围和文件哈希。

使用 `delivery-next STATE.json` 输出的实际下一动作；changed SHA退回review，不复用旧pass；平台阶段证据缺失只返回对应动作。不以旧固定37完成状态代表自动化已验收。无新证据时等待事件/人类输入，不周期重读相同文件、反复preview/catalog或重复输出“无变化”审计文件。

所有新发现最终应输出：需求编号、计划/已核成员数、合格可交付数、评分和容许差异、缺项及单一原因、正式material/resource/绑定回执及fresh状态。用户无需补逐步提示，仍能看到具体交付结果。
