# 图片翻译本地产物和保留规则

## 制作与交付

- 原图以 `fireplay-download` 登记的字节和路径为准。接入、各语言制作和核验共用这份来源。新建任务优先用 `fireplay-job`，保留原图和需求内容版本的归属关系。
- 一份已审核的无字底图可供同源适用的语言和版式共享。相同哈希直接引用已有文件及审核。冻结工作包继续用 `prepare-job`，按内容哈希归集同包输入。工作包之外的字体从既有字体目录取用。
- 新需求统一在 `work/fireplay-production/CODE/outputs/` 存正式成品，按来源、语言、成员区分路径。模型返回、局部、联系表和调试预览放在本单 `scratch/` 下，按阶段组织。审核记录放在 `reviews/`，写实际文件路径与哈希。历史需求沿用原交付路径和回执。阶段直接引用已有文件，需要把确认为同一字节的文件带到独立路径时使用 `python bin/it.py artifact-stage SOURCE DESTINATION`。相同目标可重复执行，字节冲突时使用新的修订路径。
- 有具体缺陷时只给受影响成员新增修订，底图、字体和未受影响成员沿用已有证据。发布成功的成员依原回执冻结。原始回执、审核记录和知识案例按既有路径保存。在复用回执、评分和审核证据里保存文件引用和摘要，上传所需 Base64 在调用时由程序读取已审文件生成。

Fireplay 制作目录使用固定来源编号。`fireplay-job CODE --image N` 默认在 `scratch/jobs/source-N` 建包。`prepare-job JOB` 使用 `scratch/prepared/source-N`，实际输入和配置相同便返回已有工作包。准备回执的 `background_artifacts` 指向可直接审核的文件。`render-job PREPARED` 使用 `scratch/rendered/source-N/REVISION`，同一准备版本沿用同一个渲染目录。模型原始返回、联系表和本单脚本按用途放入 `scratch/` 的子目录。

准备包各自保留可携带的依赖快照，优先使用文件系统克隆。新版准备包直接引用同包原图，以及已经校验过的外部无字底图和蒙版。只有实际生成的底图和蒙版需要新文件。相同文件的逻辑容量与文件系统实际占用分别统计。

### 模型回图和审图预览

```sh
python bin/it.py scratch-stage CODE MODEL_OUTPUT --provider image_gen.imagegen --operation HOST_CALL_ID --member source-1
python bin/it.py scratch-preview CODE PREVIEW_SPEC
```

`scratch-stage` 返回项目内路径和来源回执。相同字节共用 `scratch/model/` 下的一张图。各次操作的工具、回执 ID、原始路径和对应成员仍各自可查。同一操作处理多个来源时重复传 `--member`。续作从返回路径读取；源文件由宿主管理。

`scratch-preview` 用于实际需要查看的联系表、局部或对照图。规格文件的 `items` 按显示顺序列出 `path`，可选 `crop` 为 EXIF 正向画布上的 `[x,y,w,h]`，可选 `label` 为单行标签。外层可选 `columns`、`cell_size` 与 `quality`。相对路径以规格文件所在目录为基准。

```json
{"items":[{"path":"original.png","label":"source"},{"path":"translated.png","label":"candidate","crop":[20,30,400,200]}],"columns":2}
```

输入字节、顺序和导出参数相同时，返回已有 `scratch/previews/` 预览。改变一张图、裁剪、标签或布局后生成对应版本。预览只辅助查看，审核仍绑定实际提交文件和计划的哈希。

## 归档和回收

`config/storage-retention.json` 使用洛杉矶当地日期计算期限。结案用正式确认回执的读回日期，解除保留时从解除日期重新计时。

| 阶段 | 默认期限 | 操作 |
| --- | --- | --- |
| 结案 | 验证通过后 | 建立 gzip 查重档案，包含身份和版本、原图 SHA256、已核成员及 Fireplay 素材和资源 ID、成品 SHA256 与结案校验值。对应回执和审核 JSON 按现有账本保留。 |
| 清理媒体 | 7 天 | 把符合条件的原图、工作图、字体、本地成品和制作压缩包移到隔离目录。`runs` 中按任务来源回执验证归属。 |
| 退出本地查重 | 180 天 | 把本版本的压缩查重档案移到隔离目录并记录退役。找旧译件时可通过 Fireplay 查找并复核来源。 |
| 隔离恢复窗口 | 14 天 | 从每次移入隔离的日期算起。永久回收有单独命令，执行前可查看对应文件和字节数。 |

清理前检查当前版本的结案回执、完整复用档案、在途/未决的发布、活跃会话、手动保留、案例引用和跨需求路径引用。版本证据不齐时维持原状。文件最近有更新时按最近写入时间保留 7 天。隔离操作按原路径和哈希保存回执，移入隔离仍占用磁盘。压缩重复字节可直接释放实际空间。

| 用途 | 命令 |
| --- | --- |
| 检查后补建档案 | `python bin/it.py storage-retention CODE --archive-only --apply`。`fireplay-transition finish` 确认结案时也会尝试建档。 |
| 工作台预览到期处理 | `python bin/it.py storage-retention` |
| 复核后执行到期隔离 | `python bin/it.py storage-retention --apply` |
| 返工或临时保留 | `python bin/it.py storage-hold CODE "返工原因"` |
| 恢复已隔离内容并开始保留 | `python bin/it.py storage-quarantine CODE --apply` |
| 解除保留，重算期限 | `python bin/it.py storage-hold CODE "返工已结案" --release` |
| 预览或永久回收该单隔离内容 | `python bin/it.py storage-quarantine CODE --purge`，确认范围后加 `--apply` |
| macOS 压缩重复字体和可识别工作图 | `python bin/it.py storage-compact`，确认范围后加 `--receipt work/storage-retention/NEW.jsonl` |

维护日志和隔离回执位于 `work/storage-retention/`。本地查重候选仍需按[历史译件查重](fireplay-image-reuse.md)读取 Fireplay 的资源、适用规格和使用权限。备份时一起保存需求账本目录及正式回执。固定周期可按预览结果安排。
