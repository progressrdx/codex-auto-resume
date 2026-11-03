# Windows 适配证据与验证边界

## 新增组合

2026-10-25 核对官方 x64 安装包：
[ChatGPT-x64.msix](https://persistent.oaistatic.com/codex-app-prod/ChatGPT-x64.msix)。
该链接会随官方更新变化，以下记录只对应本次提取的文件，不代表未来版本。

- AppxManifest：包身份 `OpenAI.Codex`，版本 `26.1021.3705.0`，程序 `app/ChatGPT.exe`。
- 包内 `codex.exe` 的版本标识：`0.164.0-alpha.11.1`。本次是静态二进制核对，未在 Windows 执行 `--version`。
- `app.asar` SHA-256：`04862a659c6a90fc50514d8c0b8016709d97dece58839726c57495ed72f1d9f0`。
- `codex.exe` SHA-256：`1103e9e09c71fa2cd0afd6d8f3b9a2d63174fe8efd7cb5bd09f1cb14ef58a098`。
- 包内 `package.json` 版本不是 MSIX 包版本，不用于识别 Windows App。

## 协议核对

核对包内 `.vite/build/src-Dl21kCYQ.js`、`main-BPBEjAxi.js` 和
`application-network-startup-B4Wezh-6.js`：

- 命名管道仍为 `\\.\pipe\codex-ipc`；JSON 帧使用四字节小端长度前缀。
- `thread-owner-discovery` 版本 1、`thread-stream-following-changed` 版本 1、
  `thread-stream-state-changed` 版本 11、`thread-follower-start-turn` 版本 2。
- 发送仍定向给原任务所有者，参数为 `conversationId`、`turnStart.request`；
  `request.threadId` 必须匹配任务；省略 `context` 时继承原任务设置。
- 状态仍提供 canonical turnHistory、requests、hostId 和 resumeState。
- 可选 `thread.ephemeral` 为 undefined 时会被 JSON 省略，App 自身以 `=== true`
  判断临时会话。当前实现按已核验的 v11 快照语义将该可选字段的缺失值视为 false，
  不再绑定本次精确 App/CLI 组合；显式 null/true 仍拒绝。
- App 对尚未确认的 `unconfirmedTurnSubmissions` 阻止继续发送；本工具同样阻止，
  包括非空且标记 terminal 的记录，避免不确定发送被再次尝试。

以上是历史组合的适配证据，不是当前版本白名单。App/CLI 版本号仅用于诊断，
旧新组合或未来版本不会仅因版本号被拒绝；发送仍须通过实时协议和状态检查。
未知协议、必要状态缺失及连接期间版本变化会取消发送。未来协议变化的兼容性未因此得到验证。
最新包的检查与本次字段修复见[最新正式版兼容记录](latest-compatibility-check.md)。

## 验证层级

| 项目 | 本次新增组合 |
| --- | --- |
| 官方 x64 安装包静态协议核对 | 已完成 |
| 离线分帧、状态、发送请求和回复模拟 | 已完成 |
| 离线重置等待、重新查询额度、单次发送与去重 | 已完成 |
| 真机 Windows 命名管道与只读查询 | 未完成 |
| 真机 Windows 原任务发送 | 未完成 |
| 真机自然额度重置后自动续跑 | 未完成 |

离线测试位于 `tests/test_windows_compatibility.py`，使用合成状态和模拟管道，
不连接真实账户，不证明 Windows 真机成功。此前 README 中 Windows 11 自然重置
成功的记录针对 `26.901.31953.0` / `0.153.1`，不能作为新增组合的实机证据。

关闭用户问题前，还需在 Windows 原生环境用明确授权、无副作用的测试任务检查：
`resume.cmd doctor` 报告精确组合；原任务只读状态正常；授权续跑获得新 turn ID；
重启后不重复发送；最后实际经历一次自然额度重置。不要使用业务任务试发。
