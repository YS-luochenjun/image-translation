# 原生观感任务入口

适用于一张源图、一个或多个目标语言。Agent负责实际看图、识字、翻译、设计和迭代；这些命令负责阶段顺序、资源快照、字效、像素保护和证据绑定。用户只需给本地图片及目标语言，下面的配置和审核由Agent完成。

## 执行

```powershell
python bin/it.py doctor
python bin/it.py init-job "D:\images\poster.png" --targets "zh-CN,ja" --output runs/poster-author
python bin/it.py packet runs/poster-author/languages/zh-CN
python bin/it.py packet runs/poster-author/languages/ja
# Agent实际查看source.png，完成各manifest及job.json；不是自动识字/翻译。
python bin/it.py prepare-job runs/poster-author/job.json --output runs/poster-prepared-v1
# 在此停止。Agent实际审看background/background.png，记录background-review.json。
python bin/it.py render-job runs/poster-prepared-v1 --output runs/poster-render-v1
# 实际看各语言源/背景/字层/合成和同尺度局部，填写各自review.json。
python bin/it.py check-job runs/poster-render-v1
```

`init-job`不识字，不自动选择风格；新清单保持analysis.complete=false。`prepare-job`要求分析已完成、无未解决疑点、每区域定稿与字层映射完整、至少一项源图视觉要求。建议按方法写3～5项最影响观感的要求。它冻结完整输入，单独生成背景并停在pending；背景审核缺失、哈希过期、检查未完成或有major/critical问题时，任务及其底层渲染入口都不进入排字。保存minor问题和具体观察，不能以新字遮住背景问题。

`render-job`共享同一个准备背景，依次制作各语言独立字层和合成。`check-job`逐语言核对实际计划、语义清单、源图/字体/素材、背景审核及各产物哈希；把中文目录复制成日文目录不能通过。`pass=0`、`pending=2`、`fail=1`是check命令退出码；prepare/render成功退出0仅表示该技术阶段完成。错误中断可留下诊断目录，保留它并用新目录重做。

## 任务合同

完整实例：[中日海报任务](../examples/trueself-job.json)。它使用已归档源图、独立底图/蒙版、两份定稿和随项目保存的开放字体子集，不依赖作者的run/work目录。

顶层为schema_version=1、source/source_sha256、background、protected_regions、visual_targets、targets；可选working_scale/view_scale、notes、support_files。背景与矩形规则沿用[分层合同](layered-tools.md)。路径相对于任务JSON。语言为1～20个不重复安全标识，如zh-CN、ja；每种语言具有：

```json
{
  "language": "zh-CN",
  "manifest": "languages/zh-CN/manifest.json",
  "rows": [
    {"id": "line-1", "text": "真实个性", "font_path": "fonts/full-sc.otf",
     "font_size": 30, "center": [240,150], "color": "#f9dd8b"}
  ],
  "assets": [],
  "region_map": [
    {"region_id": "personality", "row_ids": ["line-1"], "asset_texts": []}
  ],
  "text_clear_boxes": [],
  "notes": []
}
```

`manifest`沿用[语义合同](architecture.md)，源图哈希、画布和语言必须一致。所有translate区域精确映射一次；所有rows及text/compound素材也各引用一次。按row_ids顺序连接实际row.text，再连接asset_texts声明的文字，须与target_text一致。仅做NFC、空白及CJK断句符`，。、；`规范化；数字、负号、小数点、百分号、括号与ASCII标点仍严格保留。位图文字用`asset_texts:[{asset_id,text}]`声明，并且必须实际核字；这不是OCR。装饰素材不能冒充译文。

各语言manifest.protected_regions和action=preserve区域会并入全局保护，再用于背景和每种语言合成。按实际像素变化判定，不能只靠布局矩形。语义清单也允许附在独立layered计划中，但必须同时提供target_language/region_map并完整声明语义保护区。

视觉要求每项为`{id,bbox,source_observation,criterion}`。审核模板会保留源图事实和失败判据，要求逐项填写target_observation、pass/fail/uncertain、当前已哈希的证据文件名。只把旧style勾成true而没有具体比较仍不通过；fail会阻断，uncertain/空观察保持pending。此门禁检查观察记录的完整性，不能验证审核者是否真的看图。

## 字体与可配置字效

所有长度用最终源画布像素，working_scale只内部乘一次。rows可加：

```json
"effects": {
  "face_gradient": {"top":"#ffe89f", "bottom":"#f1c463"},
  "shadow": {"offset":[1.7,2.8], "blur":1.15, "opacity":0.77, "color":"#0f0a05"},
  "contact": {"offset":[0.5,1], "blur":0.32, "opacity":1, "color":"#2d1c09"},
  "depth": {"offset":[0.65,1], "steps":4, "color":"#664114"},
  "highlight": {"offset":[0.34,0.42], "opacity":0.24, "color":"#fff2b6"}
}
```

这是本例参数，不是所有金字的默认值。每个子效果可省略，省略effects沿用原绘制；`effects:{}`启用新字面管线。已提供的子对象须包含上例中该对象的全部字段，拒绝未知字段和非有限数。字效颜色支持RGB/RGBA；渐变覆盖face颜色和透明度。shadow/contact沿画布方向偏移，angle只旋转字形；depth.offset是总挤出终点，steps均匀采样；highlight限制在字形内部边缘。对透明及半透明face须按实际预期审看。

生成`effects-face/shadow/contact/depth/highlight/glyph-mask.png`及最终text-layer。各组件供审查，多行最终按行序合成，跨行重叠时不能假设按效果类别重叠等于最终图。实际alpha超出画布会拒绝；清除保护文字alpha仍需审查是否伤字。

基础和分层入口都解析本地TTF/OTF/TTC face0的Unicode cmap4/12/13，缺字报告U+码并阻断；unsupported不被当作已覆盖。它证明字符有映射，不证明复杂整形正确。复杂文字仍需RAQM；本机RAQM=false。可单独运行：

```powershell
python bin/it.py font-check examples/assets/fonts/demo-serif-sc.otf --text "真实个性"
```

示例字体总计约200KB，仅含示例用字；新文本请选完整、许可合适的字体。许可证及来源在[字体资源](../examples/assets/fonts/provenance.json)。开发者如需重建子集，可安装可选`font-subset`依赖并运行[子集工具](../tools/subset_demo_fonts.py)，明确提供完整字体和许可证；运行项目本身仍只需Pillow。

新字效受64M RGBA等价峰值预算和200M depth像素步限制；每画布/每块及行数也受限。大图应按预算降低working_scale，或选择已验证的其他设计路线，不强行分配巨型缓存。

## 版本与资源复现

prepare目录内含inputs、semantics、冻结job和每语言plan、共享background、bundle清单及空背景审核模板。support_files可收许可证/来源说明；不会自动判断资产许可。冻结清单验证完整依赖闭包，不能删掉plan/font/manifest条目让它脱离检查。origin_inputs只作来源记录，复现不依赖旧绝对路径。

复制或移动准备包后，用其中的冻结任务重建新准备目录：

```powershell
python bin/it.py prepare-job moved-package/job.json --output runs/reproduced-prepared
```

旧回执中的绝对路径保持历史原样，因此不宣称移动旧目录后原check就自动成立。新准备目录有新回执、空审核，不继承历史pass。每次渲染和准备都使用新目录，不覆盖失败图。文字层小修可按[分层工具](layered-tools.md)复用背景，保存独立版本化语义/计划；任务级修改则新建任务快照和输出，冻结资源可直接复用，不重做有风险的背景推断。

## 差异证据与失败样本

```powershell
python bin/it.py compare examples/trueself-comparison.json --output runs/trueself-evidence-v1
python bin/it.py benchmark examples/visual-regressions.json --output runs/visual-regressions-v1
```

compare严格读取`{schema_version:1,source,candidates:[{id,path}],regions:[{id,bbox}],protected_regions?,notes?}`，同画布、同尺度输出整图、局部和精确RGBA变化mask，并记录实际文件/像素哈希。技术上只有保护区变化会判fail，允许翻译区发生变化。benchmark逐例核对expected_technical；已知视觉缺陷是有来源的标签，工具不假装自动发现“字体平”。两者视觉状态始终pending，故technical_expectations=pass不表示成图全部合格。

benchmark支持1～12例，compare最多8候选/32区域，单图16MP、总输入48MP、输出80MP等预算会预检；不同画布、动画、未规范EXIF方向、重复JSON键、未知字段、越界坐标直接拒绝。

## 完成与限制

合格交付需当前版本自审和必要的独立实际复核，报告最弱处、未解决问题及用户反馈；不将历史“好一些”套到新图。自动检查不能证明审美，更不能保证任意图片首个内部候选完美。纹理缺失时按证据换修复路线；无法可信补全便保持pending或报告能力缺口。当前没有自动OCR、无人值守视觉模型后台、真正原图/蒙版编辑API或通用透视/弧形设计能力。
