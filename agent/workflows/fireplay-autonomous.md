# Fireplay 图片翻译自主交付

2026-11-06当前通用流程。宿主Agent负责理解、看图、翻译、选路、调用连接器与真实审核；本地CLI负责矩阵、版本、评分和文件校验，不能离开宿主自行视觉生产。用户已交办的批次不逐阶段重复求许可；为用户建立本流程不等于已启动所有真实需求或获得无限付费授权。

## 1. 发现待办，不靠标题猜

执行只读 `node scripts/fireplay-discover-translations.mjs --department DEPT_ID`。DEPT_ID用当前授权组查询结果，不能凭名称猜。命令先读当前tool_catalog和requirement_create_options，从 `fixed.aiModes` 精确匹配 enabled=true、label=图片翻译，取得value；2026-11-06官方SHE选项核实为 `image_translate_or_resize`。再以 `view=summary,scope=all,dept_id=...,asset_type=[image,button],ai_mode=实际值,pagination=cursor,offset=0` 全部翻页。maxPages耗尽返回partial及next_cursor；先交给Python fireplay-discover验证该次回执，其host_actions给出保持相同过滤的续页参数；收集续页到原summary_pages再次验证，不能把局部页重新从头计为完整。缺连接器/权限报告不可用，不报“没有需求”。status不硬编码未知本地枚举，done/cancelled/canceled不作为新生产候选，其余读正文核实。

返回的summary_pages可交给 `fireplay-discover --receipt FILE` 验证。线上查询是live keyset，不是冻结快照；重复ID/编号或翻页漂移停止当前扫描，不能擅自丢弃。AI模式为空的旧任务只有用户明确编号范围/有来源分类决策才能补入；不把标题含翻译或历史旧37固定清单当自动发现范围。

每个候选精确UUID读blind_brief，并读当前delivery（必要时素材详情），核对updatedAt、正文、原图、语言、assetType和执行权限。盲选接口waiting_selection返回的空materials不是无素材，遵守其实际选择流程。当前assigneeIds是设计执行人，materials.designerIds是已存归属，uploader/launcher不是设计师。部门共享ID与需求deptId可能不同，通过官方可用共享部门资源核实，不字符串替换。发布前fresh核实权限与身份，不把历史intake当永远有效。

## 2. 规格先冻结成成员矩阵

先读[图组与尺寸规划](../../knowledge/methods/source-set-and-size-planning.md)区分已有整套逐图翻译与明确成员扩图；逐语言核来源齐全，不能只核总文件数。

| 实际规格 | 计划 |
| --- | --- |
| 大按钮 button | 明确尺寸/已接受native优先；其次同组同类已交付证据；缺省横/方源300×250与336×280，竖源300×600。PNG/JPEG≤150KiB，按官方白名单，不乘七 |
| 长方竖 image，明确要求七成员 | 每个独立创意×去重语言：1200×628×1、1200²×3、960×1200×3。先完整大横母版，再裁剪与独立层重排，七张有不同取景/构图 |
| 已提供完整参考集合，只要求逐张翻译 | provided_source_set：每个实际来源×去重语言各一张，保持各来源明确尺寸；五张就是五张，不再乘三/七 |
| 正文写具体尺寸/数量或已有接受规格 | 逐个member列出，覆盖默认；quantity字段不是自动图片数或创意数，不能盲乘 |

运行 `material-size-plan SPEC.json`；来源id、源文件SHA、去重语言、member_id、尺寸、范围依据必须对应。当前 `reference_seven_set` 需要真实七参考resource与证据；没有七参考而用户正文明确1长3方3竖时用新增 `explicit_members` 交付模式，不伪造resource_id。没有提供完整集合也没有七成员依据时，先核正文/当前组参考，再集中一次确认，不能自行把所有image扩七。

业务语言按用户提供的完整[语言映射](../../src/image_localizer/languages.py)规范化，保留原值证据，目标去重后制作。其中in→hi（印地语），id/ind→id（印尼语），kr→ko；业务别名以该表为准，不能套用通用标准把in解释为印尼语。未知/错拼如ejn、miss、翻译剩余语言不能猜，回看正文或一次询问。已交付合格SHA复用，缺项才制作。

## 3. 生产与交付取舍

制作前读[交付评分](../../knowledge/methods/delivery-balanced-review.md)。默认大致一致、画面流畅、语义正确，不要求像素复刻；用户明确精确复刻时才提高对应项要求。按[单图工作流](single-image.md)看源图识字→定稿→选择本地分层/真实编辑能力→导出→逐成员看图评分。普通背景可近似补全、装饰可简化、可合理裁剪重排；品牌、产品和人物身份、动作表达、金额条件不任意改变。母版/共用底图审核一次，同SHA复用，不为每语重建。

对明确七成员任务，建议大横母版2400×1256起步；交付小横图不能直接作为足够高的方竖母版。方竖通过独立层重排保全文案主体，不重复贴整广告凑张数。按钮独立精简布局，不把长方竖裁片硬塞尺寸。

初稿后最多两轮有假设的局部返工；总分≥80且底线通过即提交，minor无需用户再批准。不追完美、不因修复纹理不同而整个需求停摆。未达标仅隔离关联成员/源组，其他独立合格项继续；明确列缺项，不把部分交付称完成。

## 4. 发布、归属与fresh终核

使用当前官方Fireplay MCP及项目 `scripts/fireplay-mcp-publication.mjs` 的once-only journal流程：实际文件SHA+视觉审核→R2→素材入库→二创归属/共享→绑定exact requirement UUID→当前delivery/素材资源读回→逐资源下载SHA/尺寸/字节复核。所有阶段保存真实回执，逐成员记录material_id/resource_id/URL及阶段。

1. `material-size-validate` 核已输出矩阵及技术缺项。`delivery-score`每成员ready才进上传；`delivery-next` 汇总make/review/rework/upload/import/attribute/bind/verify；`delivery-gate STATE.json`从当前已审合格成员生成兼容旧发布器的gate JSON（保存到需求目录后交给gate_file），缺成员时requirement_complete仍false。字段与示例见[自动交付合同](../../docs/delivery-automation.md)。不能靠文件数或绑定数宣布完成。
2. 原始发布器要求已有intake、当前合同schema版本、gate_file、原操作UUID；它保留了历史批次的人名排除策略，普通新需求先按当前任务核实归属，不能把旧规则当所有需求默认。旧37原会话归档/unknown限制由该批次控制合同承担，不是普通发现入口的候选白名单；新需求无需等待旧会话。不要为通用自动化绕过原journal或默改归属。
   合同先从当前官方`tool_catalog`读取并审查，保存完整定义到该需求目录的`contract-工具名.json`；发布器优先使用该工具的需求级快照，再与live合同逐项比对。没有需求级文件时兼容旧全局缓存；已有文件损坏、版本不符或live漂移须刷新审查，不能静默回退或复制修改发布器绕过检查。journal同时记录实际快照路径与SHA。上传回执兼容`publicUrl/resourceId`及`public_url/resource_id`，相同别名去重，值冲突或失败回执不授权入库；同需求已审SHA来源校验及unknown不重放保持有效。
3. 图组可共用一个material多resource，button按官方单URL规则；图片数≠素材记录数。只上传新改SHA，既有合格绑定保留。
4. designer_ids依据当前明确任务归属与真实成员查证。AI模式owner不自动等于素材设计师，旧批次排除罗晨钧/邹彬不外推所有新项目。共享部门以官方有效ID为准。平台canBindMaterials和canUpdateStatus是独立权限，不自动改需求状态。
5. unknown超时不得换key重放/假reconciled；现有发布器unknown会阻断整个需求，先等官方原回执，继续其他需求。只有实际实现并验证的成员隔离才能缩小阻断，不能文档先行绕过。
6. fresh终核必须记录当前exact UUID绑定、设计师、二创、共享、真实资源全部覆盖及每个下载SHA；缺证据=verify_pending，评分达标不代表delivery_verified。

## 5. 自主续作

总并发最多四个活跃生产需求；已授权则连续取下一个可执行需求，不让中心逐个提示。单项缺原文/保护素材时一次集中问，其他独立任务继续。能力不足尝试两条可用低成本路线后报告具体缺口，不无限求完美、不未经授权收费。已确认的语义编辑写当前scope和SHA，不扩大成删人/换产品/收费授权。

使用 `delivery-next STATE.json` 输出的实际下一动作；changed SHA退回review，不复用旧pass；平台阶段证据缺失只返回对应动作。不以旧固定37完成状态代表自动化已验收。无新证据时等待事件/人类输入，不周期重读相同文件、反复preview/catalog或重复输出“无变化”审计文件。

所有新发现最终应输出：需求编号、计划/已核成员数、合格可交付数、评分和容许差异、缺项及单一原因、正式material/resource/绑定回执及fresh状态。用户无需补逐步提示，仍能看到具体交付结果。
