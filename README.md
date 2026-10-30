# 单张图片翻译框架

一个由当前 AI 宿主执行内容理解和翻译、由 Python 完成精确排版与证据管理的本地项目。第一版关注单张图片的文字准确、版式美观和主体保真，不含需求接入、上传网站、视频或音频处理。

## 已实现

- **解析与翻译工作包**：实际看图后逐区域记录原文、译文、坐标、识读置信度、保留理由和术语。数字变化、术语遗漏产生审核提示。
- **确定性排版**：按真实字体像素测量换行、字号、对齐、描边和留白，支持中日韩与拉丁文字的基础横排。最小字号放不下就报错，避免裁字或无限缩小。
- **基础背景接口**：CLI 实现均匀纯色 solid 与同尺寸 clean_plate 输入，只修改选中矩形。clean plate 可来自已审核的本地源像素/矢量局部重建或宿主真正编辑；渐变、摄影、艺术字需按图选路，不一律外部处理。
- **分阶段配置工具**：`prepare-layered` 单独生成无字背景；`render-layered` 复用背景完成倾斜/描边中文、素材合成、对照与导出；`check-layered` 校验实际文件、保护像素及真实审核。详见 [分层工具](docs/layered-tools.md)，两次案例已转为参数配置，不必每图重写同类脚本。
- **知识工作包**：packet 携带知识索引、通用方法与样式计划/阶段说明入口；案例按 tags/profile 与 source_hash 相关性读取。知识检索不等于视觉识别，也不自动执行五阶段审核。
- **保护内容**：保留区域、人物、产品、图标等保护区不得与翻译区相交。
- **可复查输出**：译图、透明文字层、前后对照、排版数据、输入/字体/输出哈希及版本化回执。
- **质量门禁**：逐区域检查语义、完整性、可读性、背景还原和风格；审核绑定当前成图，改图或改译稿需重新审核。明确区分技术完成、Agent自审和用户验收。

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

- [Agent执行入口](AGENTS.md) 与 [单图工作流](agent/workflows/single-image.md)：后续制作按此执行。
- [架构与字段合同](docs/architecture.md)：填写解析与审核记录时参考。
- [参考项目复用说明](docs/reference-reuse.md)：解释哪些方法保留、哪些视频假设被舍弃。
- [验证记录](docs/validation.md)：83项测试、两个已认可案例的配置重建及真实审核边界。
- [知识目录](knowledge/README.md)：保存有来源、适用范围和真实结果的经验。
- [命令实现](src/image_localizer/cli.py)、[工作包与门禁](src/image_localizer/pipeline.py)、[图像排版](src/image_localizer/rendering.py)：可独立扩展。

优先扩展：真实图像编辑工具适配、字体缺字检测、透视和任意形保护蒙版、设计稿文字层导出、复杂文字排版支持。倾斜排字和现有素材合成已可用配置执行；新增能力先用真实困难样例验证，避免把API成功当作画面和翻译质量通过。

基础 CLI 的边界是横平竖直矩形区域与基础横排；复杂路线按 [内容路由](knowledge/methods/content-routing.md) 选择，缺可靠能力或证据时 pending。独立底图、透明字层和五阶段真实观察需单独核实，quality 文件存在不自动证明成立。字体测量不证明字形覆盖；全图缩略、100%和局部审核还需检查 [文字构图](knowledge/methods/typography-composition.md) 的字重、黑白面积、行宽/容器关系、弧度与张力，不能只说字号相同。

合成产品海报仅是回归样例；真实 [336×280缎带案例](knowledge/cases/ribbon-20261029/README.md) 的当前艺术字重设计已获用户正向反馈，见 [版本反馈](knowledge/cases/ribbon-20261029/user-feedback.json)。case保留partial，表示单样例覆盖与已披露构图取舍，不再把旧版张力、弧形和行宽问题写成当前未处理事项。旧run/审核保留，反馈单独追加。

知识采用 [精简目录](knowledge/README.md) 与 [索引](knowledge/index.json)，项目 [薄 skill](.agents/skills/image-localization/SKILL.md) 只提供执行入口。通用流程 active，少样例技术方法 provisional，范围有限/存在取舍的案例 partial；用户反馈另行记录，不照搬视频框架、全局 skill 或 hook。
