# 项目监控与Agent评价

> 本页保留v2历史记录。当前v3.1已改为会话绑定的制作流程工作台，使用方式见[工作台说明](workflow-desk.md)。

2026-11-02，插件源码版本 `@local/image-review-ui@2.2.0` 已通过官方管理器重新应用。在左侧“图片审阅”内点击顶部“项目监控”。

## 现在能看什么

读取当前项目 runs/work 的真实产物、技术检查、Agent自审、用户反馈与benchmark证据；三类结果分别展示，不生成混合总分。可搜索、按状态筛选、15秒可选刷新，对同源同语言版本进行比较。缓存保留真实生成时间。

逐区审阅默认展示待审翻译与问题；原译一致、商标和数字等保留项折叠，仍保留各自region ID。普通行仅显示原文→定稿和状态，选中才出现结论、备注与图中定位。批量认可只改当前列表草稿，不覆盖折叠项，不自动保存。

Benchmark为紧凑表格，短名保留用例所在目录以区分报告；完整路径、SHA256、原始JSON按需展开，评价历史同样折叠。

## 每次制作耗时

新执行基础 render、render_layered、render-job 时，以perf_counter记录实际渲染耗时和UTC起止时间。独立render-timing.json绑定输出与manifest/plan指纹；文件变化后计时失效。背景制作单独标记background；native整批和各语言制作有各自证据，不能把它们重复相加成Agent总耗时。失败记录在输出父目录.render-timings，明确failed及部分输出，不覆盖已有成功记录。

任务列表和图片审阅详情通过相同绑定逻辑显示duration_seconds、duration_scope、timing_started_at/finished_at、timing_status。历史缺记录显示未采集，不从文件时间估算。Agent从识图、翻译到审核的整轮耗时、模型身份、token/成本和实时进程状态尚未采集，不能用渲染时间代替。

## 人工评价与API

image-agent-v1按语义忠实、文字风格、背景修复、全图构图、过程证据分别记录1～5分或未评分，必须填写具体观察。Agent标签为用户声明。首版成图评价要求完整三项哈希；评价追加保存，不认可后续变化的版本。

GET /api/monitor、POST /api/monitor/evaluations，经宿主认证代理公开同源 /local-image-review/monitor 与 /local-image-review/monitor/evaluations。评价绑定task_id、源图、译稿、输出SHA256；版本改变返回409且保留草稿。

## 已完成验证

Python全套213项通过，覆盖制作、计时、审阅、评测保存回读、历史及版本冲突；计时测试仅使用临时合成图，无模型或付费调用。原生插件回归2项通过。

真实认证DSH页面读到37任务、25有输出、12待制作、7份评测证据；地图中文v1/v2均加载成功；原图/译图、草稿保护、移动宽度无横向溢出。逐区默认12行/0个下拉框，4个保留项折叠，展开后独立ID完整。

浏览器表单通过CDP仅将monitor请求接到临时真实Python服务：评分保存返回201，刷新后从服务器读回；修改临时输出后提交返回真实409，草稿保留，历史仍只有1条。真实项目写入测试评价0条，未向会话发送测试消息。临时服务退出后清理样本。浏览器报告errors=[]、passed=true。证据位于项目.review-desk/verification/native-plugin/，包含report.json、review-regions.png、monitor.png和monitor-evaluation.png。

此前Windows临时目录/权限故障在用户切换完全权限后不再阻止测试；旧诊断报告保留作历史。后续若切回受限模式仍应根据实际权限状态判断，不承诺问题已从Windows机制上修复。

当前范围为本图片项目；尚未实现跨项目注册、通用代码Agent排行榜或完整端到端遥测。
