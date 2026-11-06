# 单张图片翻译框架

一个由当前 AI 宿主执行理解、翻译和实际视觉迭代，由 Python 完成分层排版与证据管理的本地项目。目标是让译图像按目标语言原生设计：语义忠实、主体保真、字形材质与画面协调。处理本地单图，并支持 Fireplay 图片翻译需求接入、原图下载与宿主调用正式连接器提交；不处理视频或音频。当前0.2版新增可配置字效、多语共享背景、逐区文字绑定和可复现输入；不宣称任意图片首稿都完全无痕。

## 已实现

- **解析与翻译工作包**：实际看图后逐区域记录原文、译文、坐标、识读置信度、保留理由和术语。数字变化、术语遗漏产生审核提示。
- **确定性排版**：按真实字体像素测量换行、字号、对齐、描边和留白，支持中日韩与拉丁文字的基础横排。最小字号放不下就报错，避免裁字或无限缩小。
- **基础背景接口**：CLI 实现均匀纯色 solid 与同尺寸 clean_plate 输入，只修改选中矩形。clean plate 可来自已审核的本地源像素/矢量局部重建或宿主真正编辑；渐变、摄影、艺术字需按图选路，不一律外部处理。
- **分阶段配置工具**：`prepare-layered` 单独生成无字背景；`render-layered` 复用背景完成倾斜/描边中文、素材合成、对照与导出；`check-layered` 校验实际文件、保护像素及真实审核。详见 [分层工具](docs/layered-tools.md)，两次案例已转为参数配置，不必每图重写同类脚本。
- **知识工作包**：packet 携带知识索引、通用方法与样式计划/阶段说明入口；案例按 tags/profile 与 source_hash 相关性读取。知识检索不等于视觉识别，也不自动执行五阶段审核。
- **保护内容**：保留区域、人物、产品、图标等保护区不得与翻译区相交。
- **可复查输出**：译图、透明文字层、前后对照、排版数据、输入/字体/输出哈希及版本化回执。
- **质量门禁**：逐区域检查语义、完整性、可读性、背景还原和风格；审核绑定当前成图，改图或改译稿需重新审核。明确区分技术完成、Agent自审和用户验收。

## Fireplay 图片翻译需求

可以让 Agent 查询 Fireplay 的图片需求、读取正文确认翻译意图，登记本地承接并下载原图，再为每张原图创建现有多语任务。入口为 `fireplay-discover`、`fireplay-register`、`fireplay-accept`、`fireplay-status`、`fireplay-download` 和 `fireplay-job`，示例和回执结构见 [接入使用说明](docs/fireplay-intake.md)。

需求查询与正式提交由宿主已有连接器执行，CLI 保存实际回执并验证分页、内容版本和交付证据。本地承接与平台认领分别记录；连接器不可用时明确报告缺口。在线发现、矩阵规划、80分交付评分及发布后核验见[自主交付流程](agent/workflows/fireplay-autonomous.md)和[自动交付合同](docs/delivery-automation.md)。下载验证真实静态 PNG/JPEG/WebP 字节、尺寸和 SHA256，之后衔接下列制作流程。

## 本地审阅台

双击项目根目录的 [打开审阅台.cmd](打开审阅台.cmd)，或在终端运行 `python bin/it.py review-desk --open`，打开 <http://127.0.0.1:8765/>。同一项目服务已运行时会复用；双击入口会自动查找项目或 Harness 的 Python。

审阅台按原图 SHA256 归组语言和制作版本，支持双图/滑杆/原尺寸对照、背景与文字层、紧凑逐区表格、筛选批量认可、追加反馈和已保存意见返工指令。反馈绑定当前原图、成图和译稿哈希，保留已有技术检查与 Agent 审核。使用方式见[审阅台说明](docs/review-desk.md)。

## 推荐的新任务入口

直接让本项目Agent处理本地图片和目标语言即可；配置、分析与审核由Agent完成。单语言也能使用这条分阶段入口。

```powershell
python bin/it.py init-job "D:\images\poster.png" --targets "zh-CN,ja" --output runs/poster-author
# Agent实际识读和定稿，配置字层、保护区与源图关键视觉要求。
python bin/it.py prepare-job runs/poster-author/job.json --output runs/poster-prepared-v1
# Agent先单独看背景并记录当前background-review.json，然后才进入排字。
python bin/it.py render-job runs/poster-prepared-v1 --output runs/poster-render-v1
# Agent实际看各语言全图/局部，发现缺口自主返工，再记录当前review.json。
python bin/it.py check-job runs/poster-render-v1
```

字段、字效、字体、版本迁移和评测详见[原生观感任务指南](docs/native-workflow.md)。可直接复现[真实中日配置](examples/trueself-job.json)，它自带小型开放字体子集、底图/蒙版及已归档源图，无本机绝对路径。

新增内容：

- **独立字效组件**：字面渐变、接触暗部、柔化投影、浅立体层与字内高光；原尺寸参数控制，单独输出供实际审看，不默认给所有图加影。
- **缺字阻断**：基础与分层入口解析真实字体cmap，找不到字形直接报告U+码；复杂整形仍取决于RAQM。
- **多语完整性**：定稿逐区映射所有实际文字，语义保留区并入像素保护；逐语言产物绑定，不能复制一份译图冒充另一语言。
- **评测证据**：同尺寸整图/局部、真实RGBA差异与历史失败样本。技术检查通过不自动判审美合格；空观察、uncertain及major问题阻断交付。

## imagegen 能否直接完成

**当前会话提供的 `functions.image_gen` 只能从文字提示生成新图，不接受原图或蒙版。它不能独立承担“精确保留原图并替换文字”。** 已配置凭证也不等于服务已实际验证。

宿主 AI 负责看图、理解、翻译、样式提取、路线判断和审核。按照 [五阶段方法](knowledge/methods/layered-localization.md)，先单独审无字底图，再审独立透明字层，合成后全图检查。常见增强操作优先用 [分层配置工具](docs/layered-tools.md)，不必每图重写脚本；基础 CLI 仍仅矩形横排，自动弧形识别、透视设计和真正原图模型编辑尚未实现。

局部源像素重建、矢量容器重建和宿主真正原图编辑都是候选，需逐图验证。当前 image_gen 可在用户授权范围内生成设计参考，必须核查文字；不能当原图编辑或忠实底图。次数与费用遵循当前授权，未知授权时不自动调用付费工具。当前没有可由 Python 自动调用的宿主视觉 API 或无人值守模型后台。

## 本机快速开始

需要 Python 3.11+ 和 Pillow 12。当前开发已使用 Harness 自带的 Python 3.12.14 / Pillow 12.3.0 实测，不需要复制参考项目的虚拟环境、模型或凭据。

在项目根目录使用自己可用的 Python；推荐隔离环境：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\python.exe bin\it.py doctor
.venv\Scripts\python.exe bin\it.py demo --output work\demo
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

以下用 `python` 代表选定的解释器。本机可以直接运行 `bin/it.ps1 doctor` 和 `bin/it.ps1 demo --output work/my-demo`，该入口依次查找 `.venv`、Harness自带Python、PATH中的Python，也可通过 `IMAGE_TRANSLATOR_PYTHON` 指定完整路径。本机PATH里的 `python.exe` 是Windows应用商店占位入口，因此建议直接用这个PowerShell启动器。

在本目录继续与 AI 对话时，可直接说：

> 把这张本地图片翻译成简体中文，保留产品和品牌，按项目规则完成解析、翻译、排版与审核。

Agent 读取项目规则后负责执行以下步骤；不要求用户手填每个字段。

```powershell
python bin/it.py init "D:\images\poster.png" --target zh-CN --run runs/poster-zh
python bin/it.py packet runs/poster-zh
# Agent实际查看source.png，填写manifest.json并处理识读疑点、翻译、字体与区域。
python bin/it.py validate runs/poster-zh
python bin/it.py render runs/poster-zh
# 复杂背景使用：render runs/poster-zh --clean-background "D:\images\clean.png"
python bin/it.py review-packet runs/poster-zh
# Agent查看原尺寸及局部对照，在命令返回的位置填写review.json。
python bin/it.py quality runs/poster-zh
```

`init` 不改动原文件：按 EXIF 方向规范化为 run 内的 `source.png`，以它的像素坐标填写文字框；同时记录原始文件哈希和规范化PNG哈希。当前面向RGB/RGBA屏幕图片，专业印刷色彩管理、CMYK保真和多页/动画另行扩展。

`quality` 的退出码：`0=pass`、`2=pending`、`1=fail`。未审的演示会是pending，这是待实际审核状态。`demo` 自身成功退出0，只表示示例生成与渲染完成；不会自动编造审核通过。

## 任务文件

```text
runs/<ID>/
  source.png                 # 规范化源图
  manifest.json              # 内容解析、定稿、坐标、保护区、术语
  packet.json                # Agent工作包，生成它不等于已经识字
  current.json               # 当前渲染版本
  renders/<version>/
    translated.png           # 译图
    text-layer.png           # RGBA透明文字层
    comparison.png           # 左原图、右译图，缩略对照
    layout.json              # 实际字号、换行及字体
    receipt.json             # 输入/输出哈希与提示
    review-template.json     # 空审核模板
    review.json              # 实际审核者填写
    quality.json             # 当前门禁结果
```

审核文件位置以 `review-packet` 回执为准；重新执行该命令只刷新模板，不覆盖真实审核。渲染总是新建版本，不覆盖原图和历史候选。

## 目录导航与扩展顺序

- [图片翻译阶段成果](docs/image-localization-milestone-20261106.md)：图组范围、分层制作、视觉交付取舍、复用与验证边界。
- [Agent执行入口](AGENTS.md) 与 [单图工作流](agent/workflows/single-image.md)：后续制作按此执行。
- [架构与字段合同](docs/architecture.md)：填写解析与审核记录时参考。
- [参考项目复用说明](docs/reference-reuse.md)：解释哪些方法保留、哪些视频假设被舍弃。
- [验证记录](docs/validation.md)：历史验证、真实双语任务、失败回归与历史案例重建；本阶段测试与覆盖范围见[阶段成果](docs/image-localization-milestone-20261106.md)。
- [知识目录](knowledge/README.md)：保存有来源、适用范围和真实结果的经验。
- [命令实现](src/image_localizer/cli.py)、[工作包与门禁](src/image_localizer/pipeline.py)、[图像排版](src/image_localizer/rendering.py)：可独立扩展。

优先扩展：真实图像编辑工具适配、透视和任意形保护蒙版、设计稿文字层导出、复杂文字排版支持。字体缺字检查和可配置分层字效已实现；倾斜排字和现有素材合成已可用配置执行；新增能力先用真实困难样例验证，避免把API成功当作画面和翻译质量通过。

基础 CLI 的边界是横平竖直矩形区域与基础横排；复杂路线按 [内容路由](knowledge/methods/content-routing.md) 选择，缺可靠能力或证据时 pending。独立底图、透明字层和五阶段真实观察需单独核实，quality 文件存在不自动证明成立。cmap检查现已验证字符映射，但不证明复杂整形或视觉质量；全图缩略、100%和局部审核还需检查 [文字构图](knowledge/methods/typography-composition.md) 的字重、黑白面积、行宽/容器关系、弧度与张力，不能只说字号相同。

合成产品海报仅是回归样例；真实 [336×280缎带案例](knowledge/cases/ribbon-20261029/README.md) 的当前艺术字重设计已获用户正向反馈，见 [版本反馈](knowledge/cases/ribbon-20261029/user-feedback.json)。case保留partial，表示单样例覆盖与已披露构图取舍，不再把旧版张力、弧形和行宽问题写成当前未处理事项。旧run/审核保留，反馈单独追加。

知识采用 [精简目录](knowledge/README.md) 与 [索引](knowledge/index.json)，项目 [薄 skill](.agents/skills/image-localization/SKILL.md) 只提供执行入口。通用流程 active，少样例技术方法 provisional，范围有限/存在取舍的案例 partial；用户反馈另行记录，不照搬视频框架、全局 skill 或 hook。
