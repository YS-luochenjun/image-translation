# 自动交付本地合同

入口：[自主交付工作流](../agent/workflows/fireplay-autonomous.md)。评分：[交付取舍](../knowledge/methods/delivery-balanced-review.md)。这些命令只读本地文件、输出JSON，不会自己识图、制造审核、上传或改平台状态。宿主Agent连续执行输出的动作，保存真实回执，用户无需逐阶段提醒。

## 命令

```text
node scripts/fireplay-discover-translations.mjs --department VISIBLE_DEPT_ID
python bin/it.py fireplay-discover --receipt discovery.json
python bin/it.py material-size-plan sizes.json
python bin/it.py delivery-score review.json
python bin/it.py delivery-next state.json
python bin/it.py delivery-gate state.json
```

在线发现每次读取当前官方选项和合同，精确AI模式+image/button过滤，读取全部游标页。保留未经篡改的summary_pages。线上分页可能没有has_more：必须显式next_cursor且query_meta.pagination=live_keyset，null才终态；旧has_more布尔合同仍兼容。失败/partial不能报告没有需求。state里的语言和member_id必须与material-size-plan输出完全一致。

## 成员规格

正文明确七成员而没有七张参考资源时，用explicit_members；不能伪造reference_members的resource_id。例如下列片段只是字段示范，scope_basis必须替换为当前真实正文依据：

```json
{
  "asset_type":"image",
  "delivery_mode":"explicit_members",
  "scope_basis":"真实需求正文明确1长3方3竖及尺寸",
  "explicit_members":[
    {"member_id":"long-1","width":1200,"height":628},
    {"member_id":"square-1","width":1200,"height":1200},
    {"member_id":"square-2","width":1200,"height":1200},
    {"member_id":"square-3","width":1200,"height":1200},
    {"member_id":"portrait-1","width":960,"height":1200},
    {"member_id":"portrait-2","width":960,"height":1200},
    {"member_id":"portrait-3","width":960,"height":1200}
  ],
  "sources":[{"id":"source-1","path":"source.png","languages":["ja","ko"]}]
}
```

每张来源×每种去重目标语言×实际member展开。provided_source_set使用每张源图原规格，不乘七；button按明确尺寸、已接受规格或官方白名单默认规划。同source/language下不同显式构图不得复用同一路径或相同字节。所有member的输出路径不共用，旧合格稿同SHA无需重制。

## 宿主真实审核输入

`review`必填：requirement_id、source_id、language、member_id；source/artifact各含path和真实sha256；reviewer含identity和kind(self/independent)；attestation含source_viewed、candidate_viewed布尔及具体notes；dimensions含以下五项，每项{score:0～5有限数,notes:实际观察}：

- semantic_accuracy、visual_continuity、overall_similarity、readability、composition。

checks每项{checked:布尔,notes:具体依据}，九项必须齐全：amounts_numbers、claims、translation_complete、protected_identity、protected_type、subject_integrity、no_visible_seams、text_not_clipped、copy_edits_preserve_facts。requirements为非空[{id,checked,notes}]，描述本任务特有要求，ID不可重复。spec含width、height、max_bytes、formats（Pillow实际格式，如["PNG","JPEG"]）。blocking_issues、minor_differences为具体字符串数组；targeted_reworks为非负整数；可选unresolved_facts为真正缺依据的事实数组。

没有源文字或身份依据记unresolved_facts，不能以空数组蒙混；有正确文案但排错字属于可修问题，不需要再问用户。合格翻译可忠实本地化并用已确认补写，不要求逐字照搬。SHA只绑定审过的字节，不证明看图；宿主真实看图后才能填这些输入。

返回status是提交门禁：ready_for_submission/rework/blocked/needs_review；next_action独立为submit/rework/blocked/review。gate blocked仍可能需要继续局部返工；未知事实或确无可行路线时挂起。80分且底线通过立即submit，保留minor说明，不反复追100。评分合同与边界回归见[评分实现](../src/image_localizer/delivery_quality.py)、[测试](../tests/test_delivery_quality.py)。

## 续作状态

```json
{
  "requirement":{
    "id":"实际UUID","code":"实际需求编号","asset_type":"image",
    "designer_ids":["当前有来源设计师ID"],"shared_dept_ids":["有效共享部门ID"]
  },
  "size_spec":"sizes.json",
  "publication_unknown":[],
  "members":[{
    "source_id":"source-1","language":"ja","member_id":"long-1",
    "output_path":"outputs/ja-long-1.png","review_path":"reviews/ja-long-1.json",
    "publication":{}
  }]
}
```

size_spec、member输出/审核、fresh下载路径相对state；审核内部source/artifact路径相对review文件。没有输出/审核的member可省略并返回make；已输出但无review返回review。证据损坏或过期仅退回该member，不拖其他独立成员。矩阵身份冲突、重复构图文件等整包冲突先纠正再规划。

同一需求包含多组图时，在 `members` 中为同组成员填写相同的 `group_id`。程序按组和语种核对素材 ID。组内张数以实际成员清单为准，大按钮保持单张入库。

`delivery-next` 从 `publication-journal.jsonl` 自动比对官方 R2 资源、按组入库结果、共享及归属和 exact UUID 的绑定结果。`evidence_counts.official_receipts` 是由回执核齐的成员数。允许设计师留空时，可接受「未识别到 DID，设计师为空」。遇到「文件名归属信息无法解析，已按未标注处理」时，批量补登二创。后续官方成功回执必须覆盖对应素材 ID，归属须符合需求，上传资源、入库和绑定也须匹配。满足这些条件即可用回执核齐该素材。全部成员核齐后由执行会话按[优化版运行手册](optimization-pilot.md)自行写回完成并确认。

有未解决的回执警告、资源变更、数量不符、历史流水字段不全或结果不明时，只用原操作 ID 对账受影响的素材，必要时单独补 `publication.fresh_verification`。旧记录仍可使用该字段，包含 `requirement_id`、`source_id`、`language`、`member_id`、`material_id`、`resource_id`、`designer_ids`、`shared_dept_ids`、`creation_type=derivative`、`sha256`、实际下载的 `downloaded_path` 和带时区的 `observed_at`。下载回执限 30 分钟内，下载路径须指回下载文件。已成功上传的成员沿用原资源。

## 对接现有发布器

delivery-gate重新评估当前state，输出旧发布器可消费的requirement_id/passed/reviewed_by/evidence/outputs(path,sha256)。将此JSON通过文件工具保存到对应需求目录，并把相对gate路径交给现有发布请求gate_file；未知写、未审/过期或未达标项不入gate。部分合格可以发布，但requirement_complete仍false。每次旧发布器仍核实际SHA、原operation_id、当前tool_catalog与原journal，不重放未知写。

发布器仍使用本地接入权限快照和真实官方合同。命令输出包含素材 ID、业务结果和警告等简要字段，可直接继续下一步。原始响应留在发布流水关联的回执文件。按当前用户规则排除罗晨钧和邹彬，其他设计师仅在身份可确认时填写，也可留空。
