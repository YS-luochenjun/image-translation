# 给“协助台”Creator 会话的交接

> 历史交接，原始未安装状态已被后续工作取代。当前维护入口为[流程台](workflow-desk.md)和[插件目录](../plugins/README.md)；本页的安装指令、版本与端口仅用于追溯，不要据此重新安装退役包。

用户已指定由本项目“协助台”Creator 会话协助完成 DSH 正式安装及宿主内验收。主会话的项目开发已完成。请从现有包继续，安装后记录实际结果。

## 可直接开始的状态

- 工作区：`C:\Users\Echo\Desktop\单图片翻译`。
- 当前 DSH：<http://127.0.0.1:64626>，已安装版本 `0.2.1-rc.5`。
- 已运行审阅服务：<http://127.0.0.1:8765/>，启用了精确来源 `--dsh-origin http://127.0.0.1:64626`。
- 可安装包：`C:\Users\Echo\Desktop\单图片翻译\plugins\image-review-desk`。
- 包名：`@local/image-review-desk`；Cordis 行 ID 和页面键均为 `local-image-review-desk`。
- 项目指纹：`decff9d24111a4a25e50239801a9ed4bb1e868111e3b0a2fa18fadd640c1ace1`。
- 当前状态：**包已完成、项目服务已运行、测试通过；尚未安装/验证 DSH 内实际页面或真实会话派发。**

## 你的接续范围

1. 先加载 Creator 环境的官方 Cordis 插件开发指南和真实检查工具，核对当前服务、槽位与包导出是否与安装包的 SDK 一致。
2. 调用官方 `plugin_manager`：`action=install_bundle`，`target` 为上述绝对包目录。用户已授权安装并应用到当前 DSH，直接按工具要求执行。保存安装回执的非敏感状态、`application` 与 warnings。
3. 根据返回状态区分已安装、已激活、需要刷新或需要重启。若重启会中断仍在运行的会话，明确说明并协调时机，不直接杀掉本项目活跃任务。
4. 在当前 DSH 确认左侧“图片审阅”入口及主面板，打开此项目会话后应出现工作台；其他项目应显示未绑定提示且不挂载此项目 iframe。
5. 确认会话列表显示本项目顶层会话，目标有可见名称及完整 ID。主会话名称可能显示为“工作台”或旧标题“解析审阅台实现与插件来源”，依据实际会话 ID 和工作区核对。
6. 在隔离测试项目/样本完成已保存意见 → 读取返工预览 → 明确确认目标 → 点击发送 → 验证目标会话已接收。不要为验收向真实图片写测试反馈。若不适合启动实际返工，先完成渲染及失败路径验收，再记录真实派发仍待验证；不要将 VM 测试说成真实派发成功。
7. 将结果写入 `docs/creator-install-result.md`，记录安装回执、激活状态、当前 GUI 地址、槽位验证、项目隔离、派发或其限制，以及是否需要用户刷新/重启。

## 已完成的实现

- [审阅台后端](../src/image_localizer/review_desk.py)：只读发现真实产物、静态资源、追加用户反馈、源图/输出/清单哈希门禁、精确宿主嵌入来源。
- [归组及返工规则](../src/image_localizer/review_groups.py)：按源图字节 SHA256 归组语言和版本；只取任务与三项当前哈希一致的最新已保存反馈。最新认可或没有当前反馈时 `actionable=false`。
- [前端](../src/image_localizer/review_web/desk.js)：折叠图片组、语言/版本切换、46px 紧凑区域行、备注展开、筛选批量认可、草稿提醒、复制返工及剪贴板兜底。
- [子页面桥](../src/image_localizer/review_web/dsh-bridge.js)：只接受配置的精确父来源及父窗口，拒绝未保存草稿及异步期间的任务/会话切换，不自动发送会话。
- [插件包](../plugins/image-review-desk/README.md)：官方 lazy factory、宿主 React、`sidebar.panellist` 与 `main` 同键注册；根目录及项目指纹隔离；确认关联会话、读取预览、显式派发，发送前再次比对反馈和三个哈希，失败保留预览并复用请求 ID。

插件 Host 是空 `apply`，当前版本不自动管理 Python 服务。插件依赖本机 8765 服务运行；不要宣称已经自启动。插件默认项目路径和服务 origin 写在客户端常量中，现阶段是此项目的专用插件；复用到其他项目需明确修改绑定并重新验证。

## 已验证，不必重复排查权限

- 完整 Python 回归：196 项通过。
- 隔离 Chrome：两张原图、多语言多版本、24 行区域、语言/版本草稿提醒、筛选与批量认可保留备注、保存回读、跨语言反馈隔离、返工复制与剪贴板拒绝兜底通过。
- 真实项目 Chrome：12 个原图组、当前组 7 种语言、46px 区域行，1440/1024/768/390 宽度无溢出，无浏览器异常。
- 子桥 Node VM：15 项通过，包含来源/窗口校验和异步会话切换拒绝。
- 插件契约及交互 VM：通过，包含重复任务消息保留预览、派发失败重试复用 requestId、跨项目隔离和卸载清理。

验证命令：

```powershell
python -m unittest discover -s tests -p "test_*.py"
node scripts/check_review_desk.mjs --fixture
node scripts/check_review_desk.mjs
node scripts/check_dsh_bridge.mjs
node scripts/plugin-contract-check.mjs
```

当前验证命令使用本机已安装的 Python、Node 和 Chrome；不需要安装 npm 依赖。截图、隔离样本和报告在 `.review-desk/verification`。

## 安装边界与已知环境事实

当前标准会话没有 `plugin_manager` 或 `cordis_inspect` 能力。官方指南已确认：Creator preset ID 为 `cordis`，只用于空白/新会话，已有 started/busy 会话不能切换；你已是 Creator，直接用正式工具完成即可。

主会话尝试桌面自动化和隔离浏览器操作，但没有得到安装成功回执。隔离浏览器只看到本机 Web 认证提示，当前 DSH 的认证文件读取也被系统拒绝；没有获取或保存任何私密认证值。用户已同意停止这条诊断路线。临时认证/桌面诊断脚本已从交付代码移除。

不要继续读取 cookie、解密密钥或排查标准会话权限；Creator 正式工具就是接续路径。不要手改用户 profile 的 `package.json`/`cordis.patch.yml` 或在 profile 手动运行 pnpm。宿主安装目录是 Electron ASAR，`app.asar/dsh` 是虚拟路径；不要当普通目录反复搜索。官方公开 SDK 的只读副本已在 `.review-desk/sdk-reference`，但你应优先使用 Creator 的实时检查结果。

## 停服后的恢复

```powershell
.\打开审阅台.cmd --dsh-origin http://127.0.0.1:64626
```

若本机 DSH 端口变化，使用它的实际 origin。若已有不支持嵌入或来源不同的旧审阅服务，先确认项目身份再协调关闭旧服务并启动新版；不杀其他项目进程。

[使用说明](review-desk.md) · [插件说明](../plugins/image-review-desk/README.md)
