# 脚本目录

在项目根目录运行。日常制图从 `bin/it.py` 进入，按[自主交付流程](../agent/workflows/fireplay-autonomous.md)调用发布器；下面的诊断脚本不是常驻后台，也不应批量自动执行。

## 共享业务入口

- `fireplay-discover-translations.mjs`：真实合同、完整分页和官方选项驱动的发现逻辑。
- `fireplay-mcp-publication.mjs`：受约束上传/入库/归属/绑定，冻结审核字节并持久化原operation UUID；结果未知先核实。
- `fireplay-image-processing.mjs`：按合同调用图片处理，费用与写入需要当前明确授权。
- `fireplay-offline-test-contracts.mjs`：合成测试合同，不能用于真实业务调用。

## 离线回归

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONPATH = "$PWD/src"
# 使用本机实际Python解释器，或已安装本项目的虚拟环境。
python -m unittest discover -s tests -q
$testFiles = @(rg --files plugins scripts -g '*.test.js' -g '*.test.mjs')
node --test --test-isolation=none @testFiles
node scripts/check_dsh_bridge.mjs
node scripts/plugin-contract-check.mjs
```

Node命令需要支持 `--test-isolation=none` 的版本；本轮使用24.14.0。调度回归从 `plugins/fireplay-session-dispatch/fixtures/` 读取有限历史样本，在临时目录模拟Host，不依赖忽略的生产回执，不执行真实派发。

## 按需诊断与实机验证

| 文件 | 用途与前置条件 |
| --- | --- |
| `diagnose_workflow_http.py` / `diagnose_workflow_snapshot.py` | 本地流程快照性能；只读，端口/预算由参数控制 |
| `check_review_desk.mjs` | 独立Web审阅台Chrome冒烟；`--fixture`使用隔离样本，需要本机Chrome/Python路径 |
| `verify_native_review.mjs` | 经插件的正式验证工具启动；需要当前已认证宿主的临时验证地址 |
| `review_fixture_server.py` / `workflow_ui_fixture.py` / `monitor_ui_fixture.py` | 隔离样本服务；后两者向父进程输出私有样本token，不保存到报告或仓库 |
| `read_dsh_public_docs.py` | 从指定ASAR读取有大小限制的公开开发资料，不能作为认证/会话读取入口 |
| `inspect-visible-dsh-session-controls.ps1` | 只读列出可见窗口控件；可能显示本机会话标题，按需运行 |

## 历史调查与迁移脚本

保留这些原始文件便于追溯；其日期、端口、路径或旧界面选择器不是当前配置。

- `inspect-size-{contracts,history,materials,values}-20261104.mjs`、`summarize-size-contracts-20261104.mjs`：一次性尺寸调查，依赖旧 `work/fireplay-readonly/` 文件；不是当前规格来源。
- `discover-unstarted-translation-launch-20261106.mjs`、`research-image-automation-contracts-20261106.mjs`：历史只读查询入口；需要当前合法连接，不在回归中联网执行。
- `verify_installed_review.mjs` / `.ps1`：旧“图片审阅”安装检查，固定端口/旧选择器；当前流程台使用 `verify_native_review.mjs`。
- `stage_native_review.py`：早期迁移脚本，旧包名断言与当前源码不匹配；不可用于重新生成现用插件。
- `diagnose_monitor_acl.ps1`：历史临时ACL工具的调用记录，所引用的临时依赖不随仓库分发；不作为可运行修复入口。

诊断输出留在 `.review-desk/` 或 `work/`；这些目录不提交。历史文件原样保留不代表已验证其当前运行能力。
