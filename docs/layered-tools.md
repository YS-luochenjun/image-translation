# 配置驱动的分层工具

这条入口把两次案例中的重复执行代码变成可复用工具。Agent仍负责看图、识字、译文与标签角色、修复依据、视觉设计和真实审核；配置不等于自动识图。现有 `init/packet/validate/render/quality` 基础接口保持原合同；本工具使用单独的计划和审核合同，不把它塞进旧manifest。

## 按难点选择工具

| 当前难点 | 首选路线 |
| --- | --- |
| 精确数字、规格、条件、普通中文促销文案 | 确定性排字；用配置调整文字、位置、颜色、字号和倾斜 |
| 白底、均匀按钮、可观察的局部平色背景 | 本地solid或逐行颜色采样；单独审背景 |
| 复杂艺术字、容器、材质与造型 | 按当前授权选择模型/设计工具生成独立素材，再配置合成 |
| 复杂摄影下的文字、无法推断的遮挡纹理 | 优先真正接受原图与蒙版的编辑工具；先验证能力与结果 |
| 原照片、商标和发布标识必须精确保留 | 声明保护范围，蒙版合成并检查源像素变化 |

当前宿主image_gen只有文字输入，不能自动编辑指定原图。它可提供独立设计资产，不应为了翻译一个按钮重新生成整张食品广告。本工具没有模型调用、凭据读取或新付费请求。将来接入真正原图编辑能力后按图评估，不坚持用脚本重建复杂未知背景。

## 最小执行过程

以下从项目根目录运行，`python`代表实际可用解释器；本机也可用 `bin/it.ps1` 启动相同参数。输出目录必须是新的，防止覆盖旧候选和审核。

```powershell
python bin/it.py prepare-layered examples/burgerking-layered.json --output work/burger-demo/background
# 实际打开 background.png；背景不干净时改background配置并另建准备目录。
python bin/it.py render-layered examples/burgerking-layered.json --background work/burger-demo/background --output work/burger-demo/render-v1
# 实际看 source/background/text-layer/asset-layer/overlay-preview/translated/comparison/view。
python bin/it.py check-layered work/burger-demo/render-v1
```

首次检查应为pending，退出码2。复制该输出内的 `review-template.json` 为 `review.json`，实际观察后填写审核，再运行check。pass=0、pending=2、fail=1。输出存在、像素相同或脚本成功不自动证明语义与视觉合格；工具无法验证人是否真的看图。

修改文字、字体或素材可复用背景，另用 `render-v2`；改source/background/protected_regions则重新prepare。旧render绑定当时计划文件的实际哈希，修改同一路径计划后旧render会报告输入变化；需长期独立复查的版本应保留其原计划路径。建议首次定稿后复制计划为版本化文件，后续改新的计划文件。准备阶段的键只绑定source、背景配置/外部文件和保护区，故纯文案变化不会强迫重修背景。

## 计划格式

[汉堡王配置](../examples/burgerking-layered.json) 可从源图重建背景和13行中文；[缎带配置](../examples/ribbon-layered.json) 复用已审核背景、蒙版和模型复合资产。所有相对输入路径以JSON所在目录为基准，output以命令执行目录为基准。全部矩形为 `[x,y,width,height]`，左闭右开；不是旧临时脚本里的xyxy。输出画布始终与源图一致。

| 顶层字段 | 合同 |
| --- | --- |
| schema_version | 固定1 |
| source / source_sha256 | 规范化本地单帧源图及实际文件哈希；方向未规范化先init |
| background | `{operations:[...]}`，或 `{path,mask}`；外部底图同画布，mask必须L模式，包含全部真实修改 |
| rows | 单行字层项数组；Agent定稿，不自动OCR、翻译或缩字 |
| assets | 已存在本地素材数组，按顺序叠在文字层之上 |
| protected_regions | `[{id,bbox}]`；比对实际源/结果像素，而非仅检查布局矩形是否相交 |
| text_clear_boxes | 可选矩形数组，最终尺寸下清除文字alpha；任何切除都警告可能伤字，必须实际审核 |
| working_scale / view_scale | 默认6/3，整数范围分别1..8/1..4，受工作像素预算限制 |
| notes | 可选字符串数组，记录语义、设计取舍和来源；不作为已审核结论 |

background.operations支持：

- `solid`：`{type,bbox,color}`，仅观察确认的均匀底色。
- `sample_rows`：`{type,bbox,donors:[bbox],row_radius,rgb_min:[r,g,b],rgb_max:[r,g,b]}`。每行从原图donor矩形内y±radius采样，通道范围inclusive，取各通道中位数并向下取整；没有有效样本就报错，不猜色。适合有可信邻近色的平色/平缓底面，不能恢复未知照片纹理。
- `restore`：`{type,bbox}`，按原图恢复局部及alpha，同时清除此处编辑mask，用于保留分隔线等原图细节。

rows每项：`{id,text,font_path,font_size,center:[x,y],color,angle?,stroke?}`。font_size为实际画布像素，可小数；角度单位度、正值逆时针；默认angle/stroke为0。center按实际裁字后的画面中心放置。字体必须真实本地路径；文字单行、非空；溢出报错，不静默裁字或压扁。复杂文字仍需RAQM，字体字形覆盖仍须真实看图。

assets每项：`{id,path,kind,position:[x,y],size?,crop?}`。kind只允许text/decoration/compound；crop先按素材坐标xywh裁切，再按size缩放。非等比缩放会警告，素材越界报错。compound明确表示文字与容器一体的栅格图形，不能称可编辑纯字层。

## 产物、检查与审核

准备目录含source/background/edit-mask、修复布局与receipt。渲染目录包含源图、背景、独立text-layer与asset-layer、合并overlay及预览、translated、comparison、view、plan/layout、receipt和空审核模板。所有旧目录保留；用户输入、字体与资产不改写。常见操作不再需要每图新增prepare/typeset/finalize脚本。

receipt保存实际输入与产物哈希、保护区变化像素、工具执行秒数。准备阶段若改变保护区或超出编辑mask立即阻断；合成保护失败会留下诊断输出但标fail，不能交付。检查重算哈希与保护变化，阻断过期审核、输入或产物变化。

审核填写真实reviewer、self/independent身份、绑定的output/plan哈希、六项bool检查（语义、完整性、可读性、修复、风格、保护内容）、源图/背景/字层/合成的具体stage_notes。minor问题如实写；major/critical或false阻断通过。素材非同比缩放、清除文字alpha的advisories需在advisory_reviews逐项真实核对后填true；任何缺项pending。用户验收仍在案例反馈中独立记录，不继承旧图认可。

计时只测命令内部执行，不包含Agent思考、配置编写、生图请求或实际审核。可对比工具重跑成本，不能宣传“新图片一秒自动翻译”。

## 迁移边界

两次历史脚本与旧审核保留作证据，不删除、不覆盖已认可图片。现已提取重复的采样修复、独立排字、倾斜描边、素材合成、对照导出和检查；缎带背景的特定重建、生成素材孤立边缘清理等仍按图处理。只有遇到现有操作无法表达的新需求，才新增小范围专用实现，并在真实样例上验证。
