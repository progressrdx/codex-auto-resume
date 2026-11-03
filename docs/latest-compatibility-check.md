# 最新正式版兼容检查（2026-11-03）

## 结论

已修复本次发现的字段兼容问题：v11 快照的可选 `ephemeral` 缺失值不再按
App/CLI 版本号处理，而是在核验本地原任务所有者和状态协议后归一化为 false。
显式 null/true、无效类型及其他必要安全状态缺失仍阻止发送。
最新正式版的静态协议检查和离线回归通过，尚未完成新版真机发送及自然重置验证。

## 官方版本与静态证据

| 平台 | 本次核实版本 | 来源 |
| --- | --- | --- |
| Windows x64 | MSIX `26.1027.6604.0`；内含应用 `26.1027.51404 / build 23880` | [官方更新清单](https://persistent.oaistatic.com/codex-app-prod/windows-release.json)、MSIX 内 AppxManifest 与 package.json |
| macOS arm64 | `26.1027.61615 / build 24141` | [官方 appcast](https://persistent.oaistatic.com/codex-app-prod/appcast.xml)、ZIP 内 Info.plist |

仅代表上述官方渠道本次返回的正式版，不包括测试渠道或不同账户的分批推送。

从官方安装包提取 app.asar，未安装或运行新下载的应用：

- Windows 包：[版本固定的 x64 MSIX](https://persistent.oaistatic.com/codex-app-prod/releases/26.1027.6604.0/ChatGPT-x64.msix)。app.asar SHA-256：`faacb124f33b792bd2c88fbe8b1215fb6869d81b145a919260e16ac11d9c1451`。
- macOS 包：[arm64 ZIP](https://persistent.oaistatic.com/codex-app-prod/ChatGPT-darwin-arm64-26.1027.61615.zip)。app.asar SHA-256：`bb88615052255e79b5977885f9a0cc212c268589587c79d4d3b7c4b46e881a95`。
- 两包的 `.vite/build/early-bootstrap.js` 均声明状态广播 v11、所有者发现 v1、跟随状态 v1、续跑请求 v2。
- Windows `early-bootstrap-499-8b3807f1185eeae8.js`、macOS `early-bootstrap-439-a0f25ba32c465de1.js` 中，续跑仍调用原会话 `startTurn(conversationId, turnStart)`，回复仍为嵌套 result。
- 同文件仍直接复制可选 `ephemeral`，App 的临时会话判断仍为 `=== true`。JSON 序列化会省略 undefined。
- Windows 本机 IPC 仍为 `\\.\pipe\codex-ipc`，四字节小端长度前缀。

## 实际执行的验证

1. 本机已安装 Mac `26.1023.50603 / build 22391`：运行当前工具 doctor，真实只读连接和额度查询成功。它不是上述最新 Mac 包，不能冒充最新版本真机验证。
2. 修复前，使用现有 ScriptedPipe 合成状态，分别注入上述最新 Windows/Mac 版本信息：缺失 `ephemeral` 时均停止，模拟续跑请求数为 0；明确提供 false 时均可被策略识别为允许续跑。
3. 修复后，同样省略该可选字段，两套最新版信息均通过完整模拟请求/响应，并分别只向原任务发出一次模拟续跑请求，获得模拟 turn ID。未来版本信息也走同一协议规则；Mac 还通过真实 Unix socket 与模拟 App 的完整交换。
4. 拒发回归覆盖显式 true/null/无效字段类型、缺失 hostId/resumeState/requests/历史、协议变化；原有审批、预算、取消和去重回归保留。
5. 上述为离线模拟。Windows CLI 版本未在 macOS 执行；未做最新版本真机发送或自然额度重置验证。

## 修复边界

字段兼容规则已按核验过的 v11 协议语义处理，无需每次更新添加 App 版本号。
此规则仅在接收来自原任务所有者的本机 v11 完整快照时生效，不应用于历史查询或未知协议。
不能把所有未知字段缺失都视为安全；
临时会话、审批状态、原任务核验、取消、预算、持久去重和不确定不重试仍须保留。
