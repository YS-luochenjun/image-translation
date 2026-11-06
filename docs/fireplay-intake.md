# Fireplay 图片需求：查询、接取与下载

入口由宿主 Agent 调用。日常可以直接交办：“去 Fireplay 查当前图片翻译需求，读取正文，接取并下载原图。”Agent 按 [执行流程](../agent/workflows/fireplay-intake.md) 完成查询与文件登记。CLI 不自带登录，不读取浏览器 Cookie 或参考项目密钥。

## 命令

```powershell
python bin/it.py fireplay-discover
python bin/it.py fireplay-discover --receipt work/image-summary-pages.json
python bin/it.py fireplay-register ABC-20261103-001 --snapshot work/image-requirement.json
python bin/it.py fireplay-accept ABC-20261103-001
python bin/it.py fireplay-status ABC-20261103-001
python bin/it.py fireplay-download ABC-20261103-001 --image 1
python bin/it.py fireplay-job ABC-20261103-001 --image 1 --output runs/ABC-20261103-001-1
```

示例编号仅用于说明格式，不能作为真实平台需求。所有 CLI 状态保存在项目根目录 `state/fireplay/`，不随终端当前目录改变。

## 查询回执

`fireplay-discover` 无参数时输出参考查询动作。拿到当前需求连接器合同后执行查询，将真实参数、完整响应保存为下列形状；每个续页保持过滤条件并增加返回的 cursor：

```json
{
  "summary_pages": [
    {
      "arguments": {
        "scope": "all", "view": "summary", "asset_type": "image",
        "pagination": "cursor", "limit": 200, "offset": 0
      },
      "response": {"items": [], "has_more": false, "next_cursor": null}
    }
  ]
}
```

若需限定组、状态、关键词等，使用顶层 `query` 承载原查询参数，页 arguments 必须与其一致。工具合同与参考不同则先调整适配器，不能修改真实响应去伪装兼容。

查询返回 `needs_summary`、`partial`、`found` 或 `empty`。`empty` 仅表示所保存完整分页内没有图片需求；`found` 中每条仍是 `needs_brief`，还未确认属于图片翻译。平台所有素材类型需通过真实 image 枚举过滤；视频记录不会进入本项目候选列表。

## 单需求回执

注册文件为 `{"summary": 原列表响应, "brief": 原正文响应}`，两者均含 `items` 数组；也可用实际 `summary_pages` 代替 summary。支持常见 `data`、`structuredContent` 和单条 JSON text MCP 封装。每个视图必须唯一精确命中编号、真实 UUID、相同 updatedAt 与 `assetType=image`。

自动读取正文/refText 中具有图片扩展名的 URL 和结构化 `locales`，支持常见语言别名与地区码去重。正文未能直接解析时，Agent 可补充带实际依据的解释：

```json
{
  "decision": {
    "translation_intent": true,
    "source_quote": "正文中实际出现的原话",
    "explanation": "根据正文与素材详情确认是图片文字翻译，解释目标语种和来源选择",
    "targets": ["zh-CN", "ja"],
    "sources": [
      {"url": "https://实际素材地址", "material_id": "真实素材ID", "source_quote": "素材回执中的实际原话"}
    ]
  },
  "material_receipt": {"items": []}
}
```

以上仅展示可选字段，真实文件同时保留 summary/brief 和完整素材响应。URL、source_quote 必须出现在真实回执中。注册不会自动理解所有业务语种、富文本附件或数量语义；有歧义时先核实。缺语言、缺来源、非翻译或关闭需求会以 blockers 留下。

## 接取、下载和制作

`fireplay-accept` 登记本地承接；它不声称已在 Fireplay 抢单、指派或改变状态。重复承接同版本幂等，内容变化需审核后承接新版本。采用一张原图对应一个现有多语任务的结构，`--image` 从 1 开始。多张图片逐张下载建包。

下载默认上限 30 MiB、5000 万像素，仅支持静态 PNG/JPEG/WebP。每次连接及重定向都验证公网地址并固定已验证 IP，保持 TLS 证书检查，不携带 Cookie 或身份头。支持带签名的公开素材直链，拒绝内网、登录页面和动画。保存真实字节的 SHA256、尺寸、格式及需求内容版本；有上游 hash/bytes/width/height 时逐项核对。下载重试保留既有原图，复用前验证其未被修改。

`fireplay-job` 下载或复用该原图后调用现有 `init-job`，生成 `job.json`、各语言 manifest 及来源记录。所有解析、翻译、背景与视觉审核保持 pending，后续按 [原生观感任务指南](native-workflow.md) 完成。

## 验证与限制

当前会话仅提供 Fireplay 投放管理工具，缺少 `requirement_list` 和需求素材详情工具。线上是否存在图片翻译需求尚未核实；当前实现提供回执驱动入口，待宿主开放需求工具即可由 Agent 联动。没有宣称零需求、线上接单成功或真实 Fireplay 图片已下载。

使用合成平台回执和受控 HTTP 响应测试分页、编号/UUID/版本、语言、重复承接、关闭需求、下载真实性、重定向、断流/超限/损坏和原图复用。测试使用临时目录；本机 DSH 受限 Python 创建的目录存在权限问题，验证需使用获准且可正常访问临时目录的执行环境。
