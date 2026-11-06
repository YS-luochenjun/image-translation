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

checks每项{checked:布尔,notes:具体依据}，九项必须齐全：amounts_numbers、claims、translation_complete、protected_identity、protected_type、subject_integrity、no_visible_seams、text_not_clipped、copy_edits_preserve_facts。requirements为非空[{id,checked,notes}]，描述本任务特有要求，ID不可重复。spec含width、height、max_bytes、formats（Pillow实际格式，如["PNG","JPEG"]）。blocking_issues、minor_differences为具体字符串数组；targeted_reworks为0/1/2；可选unresolved_facts为真正缺依据的事实数组。

没有源文字或身份依据记unresolved_facts，不能以空数组蒙混；有正确文案但排错字属于可修问题，不需要再问用户。合格翻译可忠实本地化并用已确认补写，不要求逐字照搬。SHA只绑定审过的字节，不证明看图；宿主真实看图后才能填这些输入。

返回status是提交门禁：ready_for_submission/rework/blocked/needs_review；next_action独立为submit/rework/blocked/review。gate blocked仍可能有两轮可用局部返工；未知事实/两轮耗尽挂起。80分且底线通过立即submit，保留minor说明，不反复追100。评分合同与边界回归见[评分实现](../src/image_localizer/delivery_quality.py)、[测试](../tests/test_delivery_quality.py)。

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

publication每阶段由宿主写真实已核回执：upload_receipt、material_id、resource_id、attribution_receipt、binding_receipt、fresh_verification。若字段缺失，依次输出upload/import/attribute/bind/verify。回执字段的存在只驱动下一步，不能证明平台成功。fresh_verification必须含requirement_id/source_id/language/member_id/material_id/resource_id、designer_ids与shared_dept_ids非空字符串数组、creation_type=derivative、sha256、带时区observed_at、实际下载的downloaded_path。当前版本限30分钟内；未来时间不接受。资源下载字节必须与已审输出一致，下载路径不能直接指原输出。

已审输出和当前spec必须一致且格式为PNG/JPEG；未知上传按旧发布器整需求阻断外部写，不阻本地制作或其他需求。评分通过不代表实际授权，平台仍须fresh核角色/权限/归属/共享。material_id是记录，不等于图片资源数。整个明确矩阵全部delivery_verified才complete=true，27/28仍不完整。模型自报和手填JSON无法认证平台，宿主必须实际读回。

## 对接现有发布器

delivery-gate重新评估当前state，输出旧发布器可消费的requirement_id/passed/reviewed_by/evidence/outputs(path,sha256)。将此JSON通过文件工具保存到对应需求目录，并把相对gate路径交给现有发布请求gate_file；未知写、未审/过期或未达标项不入gate。部分合格可以发布，但requirement_complete仍false。每次旧发布器仍核实际SHA、原operation_id、当前tool_catalog与原journal，不重放未知写。

本轮没有重写历史[scripts/fireplay-mcp-publication.mjs](../scripts/fireplay-mcp-publication.mjs)及旧生产账本。它有历史人名排除与本地intake权限快照，当前普通需求的设计师不能机械套排除规则，宿主按当前正式MCP/权限证据选择符合范围的提交路径。不能把本轮知识升级描述为完成全部历史需求，也不能说这是离开宿主仍能运行的图像生产守护程序。
