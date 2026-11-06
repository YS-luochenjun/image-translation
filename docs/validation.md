# 验证记录

记录范围：2026-10-29～2026-10-30。本机 Windows，Harness Python 3.12.14、Pillow 12.3.0。历史记录保留其当时范围，最新0.2版见末节。

## 自动验证

2026-10-29当时执行 `python -m unittest discover -s tests -q`：**83项通过，7.672秒**。包括既有53项、13项分层像素操作和17项分阶段编排/门禁测试。早期53项（6.637秒）和45项通过仅对应当时范围。

本次新增 [配置分层工具](layered-tools.md) 已实际重建两案例，见 [回归结果](../work/layered-regression/result.json)。汉堡王背景与合成工具耗时0.1002+0.6245秒；缎带为0.0772+0.4103秒，均与已认可成图RGBA像素完全相同。汉堡王从源图重新执行配置修复与13行排字；缎带复用已有背景/蒙版及复合图形资产，无新模型调用。计时不含Agent分析、配置编写和视觉审核。

新产物首次check均为technical pass、整体pending，未继承旧审核。制作Agent实际打开两个源图/成图对照、独立背景与透明叠层预览后记录self观察，逐项核对alpha切除和素材非同比缩放警告，再次check为pass；用户反馈仍保留在旧案例的精确版本绑定，不自动宣称新增用户验收。独立代码审查发现的JSON对象类型、缺失回执哈希映射、多帧mask三个问题已修复并新增回归。

覆盖字体像素测量、换行、最小字号溢出、保护区冲突、clean plate局部合成、LA透明度、低置信识读、缺审核pending、哈希失效、失败报告持久化及新版本重置状态。新增测试验证知识路径不能越界、条目ID唯一、正文哈希、条件案例匹配、partial状态保留及packet携带五阶段指导但不自动完成审核。fixture审核不冒充实际视觉审查。

`bin/it.ps1 doctor` 已实测可发现本机Harness Python，避开PATH中的Windows商店占位入口。doctor不调用生成服务，其本地能力报告不能代替当前宿主真实调用证据。

## 合成海报历史验证

`work/demo-refined/run` 为已知原文的1200×800自绘海报，四个翻译区域与保留品牌。已实际打开全图及五个200%区域；按钮早期纯色覆盖破坏圆角，后以绘制时保存的真实底图修正。该版self审核pass，用户验收未记录。它不证明无人值守OCR或复杂背景服务。

## 真实缎带案例

见 [部分验证案例](../knowledge/cases/ribbon-20261029/README.md)。长期证据包含原图、矩形补丁失败图、分层改善图及本轮设计候选；源图及各结果字节哈希在案例和实际审核中记录。用户认可改善，但张力、曲线、行宽关系和整体构图提出了新要求，案例继续为partial；不重写旧pass，也不将新候选记为用户验收。

本轮先沉淀方法，再实际调用一次 `functions.image_gen` / `gpt-image-2`，获得2056×765 RGBA新图形。已实际查看并核对“轻触这朵玫瑰 / 看看谁爱你”。模型只接收文字提示，未见原图；本地清理孤立透明边缘并合成至已有去字底图。**不是模型原图编辑，也不是恢复原始字体文件。**

比较A/B/C全图与局部后推荐B：更重的金属字形、明显弧形、上宽下收容器。A/C存在不同程度的旧平直底带露出，C不推荐交付。B最后下移1px保护玫瑰邻近边缘像素，最终图及放大局部已由制作Agent与独立Agent实际复核，结论为可交付设计候选，待用户确认。

[最终图](../runs/desktop-1-zh-art-direction/recommended-zh.png) / [原图—旧版—新版](../runs/desktop-1-zh-art-direction/source-previous-recommended.png) / [设计计划](../runs/desktop-1-zh-art-direction/design-plan.json) / [审核记录](../runs/desktop-1-zh-art-direction/review.json)。最终图SHA256为 `65aafd3a5b69b4ccb558b2f31b5acc02f2a8302532c07cd836534619bd1dd7ac`。

核验声明：修改在旧缎带掩模与新复合层范围内；178行以上场景未变，声明的玫瑰核心ROI未变。新容器有意覆盖部分白衣下沿和心框底尖，不能写“所有非文字像素原封不动”。密笔在336px有亮边/暗影粘连，两侧边缘略硬，是需披露的取舍。复合艺术字+容器层不是可编辑纯文字层；记录了此增强分支对阶段输出的差别，不冒称基础quality命令已验证该路线。

## 用户反馈更新

2026-10-29，用户在看到推荐版及构图取舍说明后回复“挺好的，请你进行知识沉淀与总结”。[版本反馈记录](../knowledge/cases/ribbon-20261029/user-feedback.json) 已绑定最终图哈希，认可当前视觉效果与设计方向。上文“待用户确认”属于生成和审核时的历史状态，本节为后续更新；不回填或覆盖历史review。案例partial现表示单样例覆盖和已披露取舍，不再将旧版张力、弧形、行宽反馈描述为当前未处理。

本次仅更新方法、索引、案例与反馈说明，没有修改图片或渲染代码，没有新增生成调用；此前53项自动测试记录保持其原范围。

## 方法状态与未验证边界

[五阶段流程](../knowledge/methods/layered-localization.md)为active执行要求；[内容路由](../knowledge/methods/content-routing.md)、[文字构图](../knowledge/methods/typography-composition.md)的具体技术仍为provisional。单个案例不证明各种图种均稳定成功。

- 基础CLI仍只实现矩形横排solid/clean_plate，不自动识别弧形或实现五阶段视觉审核；增强效果来自Agent实际工具/脚本。
- 本轮宿主文本生图调用已成功；未接入自动OCR或参考图/蒙版编辑API，不把该成功扩大到原图编辑能力。
- 本机RAQM=false，复杂连字/双向文字需要另一路线。密集文档、漫画、竖排、复杂摄影、印刷CMYK、复杂透视尚未覆盖真实评测。
- 字体测量、索引存在、模型返回成功、透明层或quality文件存在都不能代替语义和实际视觉审核。

新验证使用新run，保留历史失败和审核证据；生成候选不自动写用户已验收。


## 2026-10-30：0.2版原生观感工具与真实双语验证

基线在实施前提交为`0af1ea2`。本轮没有新图片模型/OCR/API调用；运行期仍只依赖Pillow。新增[字效](../src/image_localizer/lettering.py)、[多语任务](../src/image_localizer/jobs.py)、[逐区文本与保护](../src/image_localizer/job_content.py)、[具体风格门禁](../src/image_localizer/visual_review.py)、[比较评测](../src/image_localizer/evaluation.py)。用法见[任务指南](native-workflow.md)。

最终全套153项测试（基线83+本轮70）；实际执行结果与耗时保存在[本轮验证](../knowledge/cases/trueself-20261030/product-revision/validation.json)。覆盖真正缺字、OTF/TTC/cmap、透明度、阴影/旋转实际裁边、内存预算，语义Wrong/漏区/数字标点变化、语义保留区实像素保护、语言目录冒充、bundle漏依赖、底层绕过背景门禁、版本/哈希、迁移后重建、评测预算与视觉pending。fixture观察只用于测试门禁，不冒充实际视觉审核。

独立代码审查实际复现5个问题：语言目录未绑plan、定稿和rows脱节、语义保护未并入、bundle可漏plan条目、低层可绕过任务背景审核；均已修复并回归。声明语义清单的低层计划也检查region_map和语义保护，避免仅修最外层入口。

[可复现真实中日例](../examples/trueself-job.json)从归档源图、独立底图/蒙版、开放字体子集重新执行。字体两份约200KB，来源/许可证和重建工具随项目保存，生产文字须选择完整覆盖字体。缎带示例源/底图/蒙版/复合图形已移除run依赖；历史汉堡王字体仍是显式Windows微软雅黑依赖，不宣称所有旧示例均跨平台。

背景先比较局部传播、真实donor高频残差随机patch柔融合与低频约束；发现左缘亮块后，改为固定可信源墙边界重新补全。实际看过所有关键候选，保留失败图。新底图规则竖纹和亮块减少，无可辨葡文/整矩形硬边；隐藏灰泥仍是近似修复。采用的背景输入SHA为`96cd14ae8e7495a493a410c77a08d438297fe061ddc75c3323f462f7a0ed8c0f`，准备阶段RGBA PNG因编码不同SHA为`f799c90e532e935900f4b19672e7e3a2e014d892484deff121730830ff80e99c`，可见像素一致。

当前[中文](../knowledge/cases/trueself-20261030/product-revision/chinese.png)与[日文](../knowledge/cases/trueself-20261030/product-revision/japanese.png)均经过实际原尺寸/放大/独立层查看，主Agentself审核和独立视觉复核分开记录；独立结论为pass附minor、无major/critical，用户验收not_recorded。当前文字样式和上一轮已改善版接近，不能宣传又有显著字效跃升；本轮的主要新增是可复用实现、稳定阶段/文本/版本检查及墙面连续性改善。保护区实际变化均为0。

[五例回归](../examples/visual-regressions.json)含旧平字中日、当前中日和真实单像素保护损坏。预期4个技术pass、1个技术fail；预期匹配通过，所有benchmark视觉状态仍pending。旧平字技术上通过，恰好证明像素保护不能自动判审美。供给的known_visual_defects是历史反馈/独立观察标签，不是自动识别结果。

完整当前阶段、观察、哈希与限制见[本轮案例记录](../knowledge/cases/trueself-20261030/product-revision/README.md)。历史反馈、失败图及旧review不改写。没有凭本例宣称任意图片都无痕、自动复杂摄影恢复或无人值守视觉判断。

## 2026-11-03：Fireplay 图片需求接入

新增 [需求适配器](../src/image_localizer/fireplay.py)、[原图下载器](../src/image_localizer/fireplay_download.py) 和六个 CLI 入口，执行合同见 [图片需求接入](fireplay-intake.md)。最终 `python -m unittest discover -s tests -q` 实测 **341 项全部通过，66.456 秒**，包含当前既有 300 项与新增 41 项。新增测试为 [需求回执测试](../tests/test_fireplay.py)、[下载测试](../tests/test_fireplay_download.py)、[CLI 与完整接入测试](../tests/test_fireplay_cli.py)。

覆盖精确编号/UUID/updatedAt、分页连续性与空结果、排除视频、正文翻译意图、语种去重和地区码、缺信息阻断、本地幂等承接、修订和历史下载保留、并发锁；下载验证真实图片字节/尺寸/hash、公网 IP 固定、无凭据请求、重定向重新验证、断流/超限/损坏/动画拒绝、失败清理及重用防篡改。完整合成回执通过真实本地状态与下载模块建立 en-US/ja 的未审工作包，下载只执行一次，后续复用原图。

初始受限测试因 Windows 临时目录拒绝访问失败；权限诊断未修改系统权限。后在获准的较宽测试执行环境中完成全部测试。期间修复一处测试桩遮蔽真实连接函数的问题。测试响应与图片均为受控样例，没有执行真实 Fireplay 网络下载、平台认领、上传或付费模型调用。当前会话需求列表/正文/素材详情工具不可用，因此线上图片翻译需求是否存在尚未核实，测试通过不代表平台闭环。
