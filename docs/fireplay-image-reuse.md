# 历史译件精确查重

## 制作前预检

冻结原图、语种和成员矩阵后，原执行会话在首个付费制作批次前运行 `python bin/it.py fireplay-reuse-plan CODE DELIVERY_STATE`。原图按原始下载 SHA256 去重，然后逐成员比对类型、语种、成员 ID、尺寸和交付模式。一份结果覆盖该矩阵的全部语种。续作沿用同一冻结范围的结果。驳回或已关联的返工传 `--rework`，按需制作返工范围。

`matches` 是本地精确候选。按 `historical_delivery_reads` 批量读取命中文档，核实旧需求当前已完成、素材仍在可见交付内，并按候选 `material_id` 读 `material_detail` 核实 `canUse` 和资源。可将两份原始回执以 `historical_delivery` 与 `material_detail` 字段保存在证据 JSON，再运行 `python bin/it.py fireplay-reuse-stage CODE DELIVERY_STATE SOURCE_ID LANGUAGE MEMBER_ID MATERIAL_ID --evidence EVIDENCE.json`。程序优先使用通过 SHA256 和规格验证的本地已审文件，找不到才下载同哈希的 Fireplay 资源。同一成员续跑时沿用已有成品。执行会话登记返回的 `output_path`，结合本单语义和视觉要求审核，再走当前发布和结案入口。归属、共享和绑定按当前需求核验。

在本单下载原图并冻结来源、语种、成员和尺寸后，可按单张运行 `python bin/it.py fireplay-reuse CODE --image N`。查询使用当前已登记的原始下载字节。`local_candidates` 来自同类型、同源 SHA256、已结案且有正式发布回执的历史需求。每件列出历史来源 ID、语种、成员、计划尺寸、素材及资源 ID 和上传文件 SHA256。结果用于筛选，仍按本单成员、真实规格、语义、平台使用权限、归属及共享要求核验。历史回执不足的编号供定向查证。

已生成[本地轻量档案](storage-retention.md)的需求使用结案证据和档案里的精准成员映射，清理历史图片和字体后仍可查重。档案到期后，该版本从本地候选移除，可按下面的流程用 Fireplay 再查。仍处于返工或重开状态的需求按当前版本核对。

## Fireplay 素材库查找

对能省下一组制作的未覆盖原图，工作台或原执行会话可运行 `node scripts/fireplay-reuse-search.mjs CODE DELIVERY_STATE --python PYTHON_PATH`。默认选第一个未覆盖原图，`--image N` 可定向指定冻结原图。一次补查最多读 5 个相似候选，核实原始字节后反查需求面板。远程阶段最多等 45 秒，同一内容版本、规格和原图的结果写入本单 `scratch/`，续跑先读该回执。本地只保留精简结果，按实际权限核验命中的资料。查询慢、结果有限或材料权限不足时，独立成员可以继续制作。

1. 本地缺少可用候选时，宿主按 `image_lookup` 读取实时工具合同并执行一次官方图搜图。若只给出 `reference_file`，按官方合同用其 Base64 查询。现有素材库图搜图使用配置的向量服务，按检索权限返回候选。
2. 将 `source_sha256` 和真实 `image_search` 响应放入本地 JSON，执行 `python bin/it.py fireplay-reuse CODE --image N --lookup LOOKUP.json`。宿主调用返回的 `resource_lookups`，把原始响应按数组填入同一 JSON 的 `resources`。
3. 再执行同一命令。程序核对命中的具体资源，校验尺寸和可用 MD5，再按原字节上限读候选并比对 SHA256。仅完全相同的原图素材进入 `requirement_lookup`。按结果中的分页参数读当前可见需求。`source` 关系可定位同源候选，其他关联按旧正文及原图证据核实。截断、不可见、盲选和读取失败按现有官方流程处理。
4. 对命中的旧需求读 `delivery`，将历史候选按当前矩阵逐成员核验。批量预检可接收 `--lookup LOOKUP.json`，有 `requirements` 回执时生成精确关联的旧需求读取动作。需求面板的标题、描述、引用和交付关系供定位，进入同源候选要求 `source` 关系。目标语以实际内容为准，Tag 辅助定位。用当前 `material_detail` 核对实际资源及 `canUse`。驳回返工单按本单异议处理，普通单其余成员走正常制作。

## 发布衔接

当前已接入重新使用已审图像字节，再按新需求审核及发布。Fireplay 单独找到的旧需求可供定向核验，批量预检只有在原图及成员映射完整时才提供可暂存的精确候选。跨需求直接绑定旧素材 ID 还需要发布器和结案回执识别复用成员，以及核验整组资源、归属、共享和素材使用权限。
