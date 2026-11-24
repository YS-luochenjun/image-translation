# 图片翻译长期巡检与续办

用于当前图片翻译项目的宿主主控会话。配置在 `config/fireplay-monitor.json`，实际会话、自动化 ID、扫描证据和派发账本在 `work/fireplay-monitor/`。这是一套宿主执行约定，JSON 本身不是后台进程，也不证明已发现、制作或发布成功。使用官方自动化工具管理定时唤醒，使用官方聊天工具管理执行会话。

用户2026-11-11在视频主控要求两个项目讨论解决图片项目空轮询，并采用增量巡检。2026-11-13用户明确要求替换原图片轮询，当前唯一中心工作台为 `01a1eeef-d721-7a80-b5c0-62af43bac945`，自动化 `30-2` ACTIVE；旧主控 `01a1d42c-205d-7950-b783-e1880917da2b` 只保留历史。原主控 `01a1c978-41cc-7e62-a34f-977b32f2c849` 的 `fireplay-2` 已 PAUSED，历史 `fireplay-3` 继续 PAUSED；保留原ID、历史、检查点、需求负责会话和未结操作，不自动恢复旧轮询，不新增任务级完成盯办自动化。实际主控和自动化登记以 `work/fireplay-monitor/controller.json` 为准。发现与上传终核保持独立阶段，由同一主控按实际账本执行；空账本跳过聊天进度查询。有任务时核原负责会话，用户要求跟到完成时在当前主控持续核对。只有新需求尚无负责会话时才建立需求编号会话。额外建立的 `01a1c97f-065d-7193-a44f-cbebb2fbe2cb` 只保留历史。

用户最新要求：已实际完成的需求不再跟踪；按需求编号日期或可信需求日期，以 America/Los_Angeles 当地日期计算，超过三天的不再跟踪、派发或重新接入。历史记录保留；平台 done 但已知交付未齐仍按未完成处理。无法确定日期时不得猜测。

## 首次启动

派发与终核统一使用标准长方竖图口径：每个独立创意×该来源语言固定7成员（1长3方3竖），无需正文再写标准输出。现成完整七成员只翻一次，任意多张横图不算完整套。派发必须写明来源分组、语言和应交成员总数；完成核对须逐套检查七成员。仅明确用户特殊范围可例外，已审成员复用、缺项补齐，partial不结案。详见图组尺寸规划。

先读 AGENTS.md、image-localization skill、fireplay-autonomous.md、fireplay-polling.md、fireplay-intake.md、交付评分及图组尺寸规划。运行 doctor，使用实际可用 Python。读取 controller.json 的真实主控身份与自动化登记；当前控制器不可自行重建另一个控制器。

本次用户要求参照视频项目建立图片翻译自动化会话，授权本范围查询、建立按需求编号命名的执行会话、派发和续办。用户请求原文在创建来源聊天中，以实际 read_thread 为准；本文件不是新增人类批准。视频的人员筛选、八项并发、付费额度、特殊接替及回传授权不迁移。其他项目已停止的巡检保持原状。历史 37 批次及未复制到当前 checkout 的原产物不能假定存在或已迁移。

## 每30分钟发现

1. 先核账本和当前负责聊天，合并尚未处理的变化。使用 tool_catalog 取得 requirement_dept_list 和 requirement_create_options 的当前完整合同，通过 tool_read 读取可见部门和真实选项；部门 ID 只作选项种子。精确匹配 enabled=true、label=图片翻译 的实际 value，不写死旧枚举。
2. 日常 `new` 通过 `requirement_list` 查询当前图片翻译 `not_started`，使用 summary 视图、全可读范围、图片和按钮类型、40 行游标完整分页。每24小时用 `full` 查询相同范围的所有状态，每页 10 行。已经派发的需求另按 exact UUID 跟踪。保存实际参数、原响应、serverTime 和游标，完整且校验通过的分页才能推进检查点。
3. 当前 `scripts/fireplay-discover-translations.mjs` 的 transport 依赖 Windows LOCALAPPDATA/DPAPI；本 Mac 不复制认证文件，不读取参考项目凭证。直接通过已连接的官方 MCP 完成同等查询并保存 summary_pages，再交给 `fireplay-discover --receipt` 校验；不可把 Windows transport 缺失当作整个 MCP 不可用。
4. 缓存以UUID、updatedAt和summary元数据指纹比较；符合三天范围且未实际完成的新/变化候选读exact UUID的blind_brief和delivery。未处理的brief保留待办，不能因先更新缓存、下次summary相同就丢掉。正文/附件/语言变化才调整制作矩阵，元数据变化不自动重做。核正文、全部有效来源、去重语言、类型及canBindMaterials；原文/语种缺失、identity_pending、unknown或已接入/排队需求写入assignments，之后每轮exact UUID每批最多40跨状态读取summary，不带status、asset_type、ai_mode或日期过滤。其模式/状态改变、查询缺失或权限变化都保留待核，不能当作完成。done/cancelled/canceled且无本地owner或未结操作的历史项只留缓存，不读详情、不进常规跟踪。not_started也不能证明未交付。
5. 持久主键是 exact UUID，并记录需求编号。检查当前账本及已有聊天；必要时查历史/归档聊天，读真实上下文后确定唯一负责者。编号相同先核身份，图像近似不能直接判重。缺历史登记时不批量接管已有需求，先查已有绑定和负责会话，无法核实的标 identity_pending，独立新需求继续。
6. 冻结实际来源×语言×成员及规格。每轮按 `new_candidates` 的顺序调用 `fireplay-accept`。容量 20 单，超额候选留在发现账本，计划输出 `capacity` 和 `queued_candidates`。取得名额并确认 Fireplay 开工后，用需求编号创建或续用原会话。取得 threadId/hostId 和启动回执后登记派发。新建会话使用宿主默认模型和推理等级。

## 有工作时跟踪已有需求与上传

由30分钟巡检中的独立跟进阶段处理已登记会话；fireplay-3保持暂停。先运行下方计划命令，chat_targets为空时不调用read_thread/wait_threads，不为旧closed需求轮询聊天。有待办才用原会话续作，补图、补语言、补上传均不建接替聊天；当前用户要求持续跟到完成时，在本次主控任务中继续观察直到终核或真实阻塞。

读取同一账本，用 compact wait_threads(timeoutMs=0) 和保存的游标核负责会话。运行中的会话继续制作。idle/error 且有明确下一步时，沿原聊天发送一次具体续作交接，保留 read_thread、实际发送回执和唯一信号。派发前记录意图，未知发送或创建结果先核对原操作。出现空位时按优先级和时间顺序补位。

每次发现负责会话已停止或有新结果，核对其最后回合、实际产物与平台回执，并及时更新 assignments 的实际阶段、阻塞原因、已完成数量和下一步；不得让过期的 dispatched 状态掩盖已停止、部分交付或真实审批拒绝。会话完成不等于交付完成，单纯发送“继续”也不能解除权限、网络、素材缺失或质量阻塞。按用户授权处理 SSE idle timeout 接替，先核原会话和未知写入，避免出现多个实际负责人。

代理报 `MCP RPC rejected` 本身不说明服务不可用：优先使用当前会话提供的官方连接器。对必要的只读查询，若有沙箱网络受限迹象，按宿主权限流程申请联网并重试读取；确认结果前不得把本地网络错误归结为平台故障。写操作仍按原操作回执及官方限制处理，不能将此诊断作为绕过审批或重放未知写入的理由。

新增来源或语言只补当前矩阵缺项；updatedAt 变化先比较实际内容，不因素材绑定或状态更新时间重做。相同来源 SHA、译文、规格及当前审核仍有效才复用。原会话、预算、返工轮次、unknown 和发布 journal 持续保留。技术错误按真实回执处理，不复制视频项目的重试次数或费用上限。

## 执行会话交接

传入 exact UUID/编号、brief/delivery 原回执路径、来源和语言证据、成员/尺寸计划、唯一负责者、开工或排队状态、当前下一步与已有产物/发布 journal。要求先真实打开源图再识字翻译，背景审核、独立字层、合成与适配按本项目路线执行。每成员绑定当前 SHA，≥80 且全部底线通过立即提交；有可行改进路线时持续局部返工，不设固定次数，受阻成员不阻止独立合格成员。

用户已明确允许已交办图片需求使用付费内容和工具，包括 KIE 或 GPT ImageGen，按真实工具合同、实际费用与宿主审批执行并记录；不因缺少项目内逐单付费授权停止。单纯缺精确底图不立即挂起，先尝试可用编辑和修复路线，质量不达标时继续改进。

用户2026-11-19明确知情同意已交办图片需求经 Fireplay 官方接口上传，允许生成公网可访问图片地址，并授权素材入库、共享、需求面板绑定及核验，无需项目自行逐次征求确认；R2里已有重复对象无需清理。用户后续要求已成功上传的具体成员冻结，不因重复对象可接受而重传或替换。发布复用当前官方工具及项目 journal/hash 门禁，保留真实 R2、入库、设计师/二创、共享和 exact UUID 绑定回执；结果未知的写操作按官方工具要求先对账。用户新授权后可按正常审批流程重新申请此前拒绝的操作，不可把用户授权当作宿主审批成功或绕过再次拒绝。Mac transport 不兼容时先在执行会话解决明确接口适配，保留原操作身份，不能绕过门禁直接宣称发布。只读发现成功不代表该发布链已验证。

用户明确当前不提供无字原图；去除原字、重建并审核底图是执行会话的职责。不得仅因没有无字原图而要求用户提供或结束任务，应以现有源图使用已授权的本地或图片编辑工具继续修复，不达标时更换可行路线；真实不可解决的工具或质量问题只阻塞受影响成员。

用户进一步要求复杂摄影去字优先采用 Codex ImageGen 参考图编辑或查询 KIE 的合适原图编辑能力，不为省钱默认先试本地 LaMa/OpenCV。已交办图片任务合理使用付费工具，无项目自定逐单费用或调用次数限制；保留真实回执并遵守宿主审批。执行会话必须读取 [图片生成与去字路线](../../docs/image-generation-policy.md)，历史 LaMa/OpenCV 失败文件只作反例，不能继续当作主路线。检查停下的负责人时按此路线续办，已在正常执行者继续运行。

冻结按“具体成员”生效，而不是等整组完成：只要某个成员获得官方上传成功回执（R2/resource URL 或 resource ID），立即写入冻结清单。之后的入库、归属、共享、需求绑定和 fresh 核验只能读取、补登记和核验，禁止执行会话因为视觉意见、模型路线、是否使用 ImageGen、版本偏好或整组补齐而重制、替换、追加版本或重新上传该成员。只有用户明确要求返工，或官方回执明确要求修正时，才建立新的修订范围并保留原提交。整组中尚未成功上传的成员仍可继续制作和提交，不能修改已经成功上传的成员。

交接与上传终核顺带落实[素材Tag约定](../../docs/material-tags.md)：语种沿需求原写法如`ja`、`hi`；明确尺寸、产品/主题词和需求编号按方便搜索标注。缺项沿原需求会话保留已有标签补齐并读回，不强制中文语种名或复杂格式，不因本次规范重做已完成需求。

## 回执驱动计划与检查点

`scripts/fireplay-monitor-plan.mjs` 只读本地账本、校验已保存的官方回执和原子保存缓存；不调用MCP、不生产图片、不另起后台。宿主先解析当轮官方ai_mode，再运行（node不在PATH时使用宿主提供的Node可执行文件）：

```sh
node scripts/fireplay-monitor-plan.mjs --state work/fireplay-monitor/discovery-state.json --assignments work/fireplay-monitor/assignments.json --ai-mode CURRENT_VALUE
```

按照 `discovery_query` 及 `tracked_queries` 调用官方 `requirement_list`，完整保存每个查询的 `{query,summary_pages:[{arguments,response}]}`。未知写入沿原操作对账，开工与结案步骤见[优化版运行手册](../../docs/optimization-pilot.md)。

```sh
node scripts/fireplay-monitor-plan.mjs --state work/fireplay-monitor/discovery-state.json --assignments work/fireplay-monitor/assignments.json --ai-mode CURRENT_VALUE --receipt SAVED_RECEIPT.json --mode new
```

full使用`--mode full`；tracked使用`--mode tracked --ids UUID,UUID`，必须和计划的原始批次一致。tracked每批保留最多40个exact UUID，每页只取7条以避免宿主响应截断；沿next_cursor保持原UUID批次和全部过滤条件，不能拆批拼造原查询回执。active/full检查点独立；tracked按exact UUID批次分别记录，缺失观察保留其他批次，部分批次完成不能称整轮全部成功。缺页/错误不写缓存或成功检查点，原回执仍保留。tracked缺失列表不授权移除原owner/unknown。新增本地巡检时效约定：每页serverTime须在本轮接收时30分钟以内、不得晚于当前时刻，updatedAt不得晚于serverTime；brief/delivery确认已读也核时效及同版本。旧回执保留原日期作历史证据，不冒充本轮查询。`--now`仅用于冻结当轮真实观察时刻或离线测试，不回拨时钟刷新旧记录。记录是live观察而非冻结快照；缓存不证明已制作或交付。

`brief_candidates`仅列未完成的详情读取。先把接入、缺信息、超范围或待核结果写入原UUID账本；已读取同版本brief/delivery后，把真实回执存为`{summary_hash,blind_brief,delivery}`，以`--acknowledge UUID --review FILE`确认已读。两view必须scope=all、仅含该UUID/编号且updatedAt与待办一致；有变化先刷新summary再读。确认已读只清详情读取待办，不能将盲选/权限/制作/上传阻塞标为交付完成。禁止用CLI自行构造平台成功回执。配置或账本损坏直接报错，不退回空账本覆盖。

## 终核、通知与停止

逐成员核当前文件 SHA、真实尺寸和字节、评分、设计师（可留空）、二创、共享、正式 material/resource ID、绑定和下载。执行会话的 self 审核、主控抽查和用户认可分别记录。全体成员核验通过后由主控按[优化版运行手册](../../docs/optimization-pilot.md)写回并读回「已完成」。

只有新接入、已核实完成、重要失败或确需人工动作时通知。全可读范围受服务端权限限制，不称全公司全量。没有可行动变化保持安静。队列为空仍保留30分钟需求发现，跳过聊天轮询；历史20分钟盯办已按本轮用户要求暂停，不自动恢复。不得唤醒其他项目已停止的自动化。

`controller.json` 记录真实主控/来源聊天、自动化 ID 和 verified 状态；`assignments.json` 以 UUID 保存负责聊天、状态、当前矩阵/证据路径、游标、最后实际动作和待核实操作。首轮扫描成功后才填写 last_successful_scan，不用建立配置的时间冒充扫描时点。所有回执保存在本地忽略目录，不写入共享知识库凭证或外部通知。
