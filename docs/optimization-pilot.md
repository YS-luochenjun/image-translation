# 图片翻译优化版运行手册

从桌面的独立项目根目录运行。试点范围是用户交办的具体图片需求。先核生产主控是否已派发该需求，沿原负责人续办已承接工作。优化副本的发现状态、上传操作和完成核验各自保存实际回执。

## Fireplay 从开工到完成

1. 主控用 `scripts/fireplay-monitor-plan.mjs` 生成查询。首次试点在 `work/fireplay-monitor/` 建立 `assignments.json`，初始内容为 `{"requirements":{}}`。日常 `new` 查询只取官方图片翻译选项下的「未开始」，每 24 小时 `full` 跨状态对账，已接入项用 `tracked` 按 UUID 读取。按计划的 `discovery_mode` 保存完整分页回执。`new_candidates` 是待核候选。拿到正文、来源、语言、当前负责人及交付记录后，才确定由本项目接手。
2. 用 `fireplay-register CODE --snapshot INTAKE.json` 保存当前接入版本，再执行 `fireplay-accept CODE` 本地承接。优化版最多占用 20 个需求名额。`queued_capacity` 的候选留待下一轮，本单仍在承接前。准备 `STATUS.json`，其中 `summary` 与 `blind_brief` 分别是当前实际官方 exact UUID 查询结果，`options` 是 `requirement_create_options` 的实际回执。两次列表查询使用 `scope=all` 和对应视图。
3. 运行下方开工命令。宿主用当前官方接口合同核对 `host_action`，在实际授权内调用一次 Fireplay。保存真实写回响应，重新取得该 UUID 的两份视图，将响应随新的观察 JSON 保存，用 `--reconcile` 读回。确认 `in_progress` 后再派发。已有负责人的进行中需求沿原会话继续。
4. 执行会话逐张制作和审图。用 `delivery-next` 读取下一步。和 `delivery-state.json` 放在同一需求目录的 `publication-journal.jsonl` 保存官方资源及各成员绑定。官方成功上传的图片优先从原回执续办入库、归属、共享、绑定与下载校验。
5. 主控在所有成员都核验后重新注册当前正文。`delivery-state.json` 的 `content_version` 使用该交付清单所对应的本地承接版本。将同版本的当前 `delivery` 视图加入 `STATUS.json`，用 `finish` 准备一次写回。预览和写回前均重新检查交付清单及面板上的绑定素材。写回后按同一操作读回「已完成」，再登记本地结案。

```sh
python bin/it.py fireplay-transition CODE start STATUS.json
python bin/it.py fireplay-transition CODE start STATUS.json --prepare
python bin/it.py fireplay-transition CODE start READBACK.json --reconcile
python bin/it.py fireplay-transition CODE finish STATUS.json --delivery-state work/fireplay-production/CODE/delivery-state.json --prepare
python bin/it.py fireplay-transition CODE finish READBACK.json --reconcile
```

`--prepare` 持久登记一个操作并只返回一次写回参数。遇到超时或写回状态冲突时，按原操作读取实际结果。已有人负责或权限不足时，主控按当前实际情况记入待核。`canUpdateStatus` 和 `canBindMaterials` 各自决定对应操作权限。平台「已完成」可能发出素材交付通知。

`work/fireplay-monitor/admissions.json` 保存承接名额，和 `assignments.json` 按 UUID 去重。开工待确认、仍需续作及写入结果待核的需求占用名额。完整交付核验后的结案读回释放名额，主控已终核或已核实撤销的记录也释放名额。超过三天的历史需求仍按跟踪规则处理，已有占位保留至主控核实交付或撤销。排名先看优先级，同优先级用创建时间、更新时间和编号排序。

主控计划的 `continuation_candidates` 列出已有名额、仍无执行会话、需要核对原开工状态的需求。先查原开工操作并确认状态，再沿原记录完成派发。

## 制作与续办

```sh
python bin/it.py packet runs/JOB/languages/ja
python bin/it.py packet runs/JOB/languages/ja --since CHECKPOINT
python bin/it.py review-packet runs/JOB/languages/ja --since CHECKPOINT
python bin/it.py delivery-next work/fireplay-production/CODE/delivery-state.json
python bin/it.py delivery-next work/fireplay-production/CODE/delivery-state.json --since CHECKPOINT
python bin/it.py prepare-job runs/REVISION/job.json --output runs/PREPARED-NEXT --reuse-background runs/PREPARED-PREVIOUS
```

首次工作包返回 `mode: snapshot`，列出当前提示词、知识方法和当前数据。续办把该命令上次的短版本号传给 `--since`。无变化返回 `mode: unchanged`，有变化返回 `mode: delta`。文字区和交付成员分别按身份返回 `upsert` 和 `remove`。交付初次只显示前 8 个待办，后续比较覆盖整个成员清单。缓存缺失或版本过期时返回当前快照。用 `--full` 展开完整工作包、审核提示词或交付动作。增量用于阅读，实际修改以磁盘上的 `manifest.json` 为准。

多语种 `init-job` 将源图规范化一次。冻结输入和共享背景优先使用支持写时复制的文件系统，其他文件系统用普通文件复制。`--reuse-background` 只复用技术输入、保护范围、实现版本与实际字节一致且已有合格审核的底图。复用记录绑定原底图审核，新译文字层和最终成图仍需实际审核。

上传使用 `delivery-gate` 的精确成员及 SHA。发布脚本只读目标文件字节。首次入库可用原上传操作 ID 证明素材来源，带 `gate_file` 时额外核对原上传 SHA。已登记素材优先沿原素材 ID 做后续操作，需返工时先建立有真实依据的修订范围。

新发布操作的原始响应保存在 `publication-操作UUID.json`。流水记录文件哈希，读回时校验响应身份和字节。原有内嵌响应的流水仍可读取。

## 记录耗时与费用

`record-stage` 读取一份实测记录并追加到需求目录的 `stage-events.jsonl`。`stage-summary` 汇总总跨度和每阶段用时。并行阶段的秒数可以相加超过总跨度。

```sh
python bin/it.py record-stage work/fireplay-production/CODE/stage-events.jsonl EVENT.json
python bin/it.py stage-summary work/fireplay-production/CODE/stage-events.jsonl
```

事件字段为 `stage`、`started_at`、`finished_at` 和 `outcome`。阶段可用 `intake`、`download`、`background`、`lettering`、`composite`、`provider_wait`、`visual_review`、`rework`、`upload` 或 `readback`。时间写带时区的真实时间戳。费用用实际回执的 `cost.amount`、`cost.unit` 和证据路径 `evidence`，可附 `member` 便于定位。
