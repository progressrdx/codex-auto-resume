# Codex Auto Resume 兼容检查（2026-11-22）

## 当前版本的影响

Codex Auto Resume `0.3.5` 在 macOS `26.1117.41037` 上发现了续跑协议兼容性风险。当前实现通过旧版桌面 IPC 发现原任务所有者、获取实时快照并向所有者提交续跑请求。本机新版安装包的 JavaScript 中已找不到这几条旧消息，无法确认新版仍提供该链路。这个版本的自动续跑应视为未通过兼容性验证。

| 检查对象 | 版本与结果 |
| --- | --- |
| 本机 macOS arm64 | `26.1117.41037`，build `29803`，内置 CLI `0.171.0-alpha.13`。完成静态检查及隔离只读查询。 |
| Windows 正式渠道 | [官方更新清单](https://persistent.oaistatic.com/codex-app-prod/windows-release.json) 列出 MSIX `26.1117.4957.0`，含 x64 与 arm64。本次仅核实发布信息，尚未检查安装包协议或在 Windows 真机验证。 |
| 独立 Codex CLI | [官方更新日志](https://learn.chatgpt.com/docs/changelog) 列出 `0.171.0`。工具使用桌面 App 内置 CLI，独立 CLI 的版本及其 `exec --remote` 功能无法验证桌面续跑链路。 |

### macOS 已核实的证据

- 检查本机 `app.asar` 中 9,893 个内嵌 JavaScript 文件，未找到 `thread-owner-discovery`、`thread-stream-following-changed`、`thread-stream-state-changed` 或 `thread-follower-start-turn`。包内 `.vite/build/app-server-host.js` 可见 `thread/turns/list` 和 `turn/start`。这些接口的存在不足以证明能核验原任务所有者、实时审批状态和发送去重。`app.asar` SHA-256 为 `b9e516de7c5ef608bf83a6313c815f5a7ef8819e4f08aef04c69744ecfc59cf8`。
- 新版内置 CLI 保留 `app-server --stdio`。用临时 `CODEX_HOME` 运行工具的只读客户端，`initialize` 和 `thread/list` 均成功，空目录返回零条会话。真实账户的额度查询仍未验证。
- 用现有账户目录运行 `./resume doctor` 时，查询子进程退出。单独启动子进程和连接桌面 Unix socket 都得到 `Operation not permitted`。本次执行环境的权限限制使真实只读验证未能完成，单凭该错误无法判断桌面协议是否响应。
- 尚未完成新版真实发送或自然额度重置验证，旧版的成功记录继续按原版本保留。

### 已适配的读取与诊断

- 新版采用分页历史的会话通过 `thread/read` 核验 UUID 和历史类型，再通过 `thread/turns/list` 只读取最近一轮，不加载条目内容。旧会话仍使用旧历史读取方式。历史结果只参与托管资格筛选，发送仍需要桌面实时校验。参见[官方 App Server 文档](https://learn.chatgpt.com/docs/app-server)。
- 新版额度返回 `ordinaryUsageAllowed` 时，只有明确为 `true` 且通过原有额度窗口检查，才确认普通用量恢复。`false` 或 `null` 等待重新查询。旧版额度响应仍按原有规则处理。字段语义来自本机内置 CLI 生成的 schema 和[官方协议定义](https://github.com/openai/codex/blob/main/codex-rs/app-server-protocol/schema/typescript/v2/GetAccountRateLimitsResponse.ts)。
- `doctor` 分别报告额度读取和旧桌面握手的结果。即使握手成功，`autoResumeAllowed` 也不会因这次通用诊断变为 `true`。
- 直接使用 `turn/start` 无法保证新一轮只在指定的旧轮次和审批状态下启动。内置 CLI 生成的 schema 还说明请求可能向正在执行的轮次追加输入。当前自动发送仍依赖可核验的桌面接口。

### 新版续跑接口核查

- 本机内置 CLI `0.171.0-alpha.13` 支持 `codex queue`。官方文档说明 `--resume` 失败时命令仍可成功退出，因此 CLI 退出码不能作为原任务已启动的回执。参见[官方 CLI 文档](https://learn.chatgpt.com/docs/cli/reference#codex-queue)。
- 本机生成的 schema 中，`thread/queue/add` 返回待执行项，`thread/queue/start` 才返回启动的 turn。入队请求没有预期前一轮或审批状态条件，启动请求只有任务 ID 与可选队列项 ID。官方测试还覆盖了任务忙时启动被拒、已入队消息后来随任务空闲开始执行的情况。直接用入队替换原续跑会使延迟执行脱离工具的取消和状态核验。参见[官方队列测试](https://github.com/openai/codex/blob/main/codex-rs/app-server/tests/suite/v2/thread_queue.rs)。
- 新版桌面 App 的本地引擎管理代码仅在满足条件时使用共享 daemon；另一条路径通过应用自身的标准输入输出连接引擎。本机执行只读 `app-server daemon version` 返回控制套接字不存在。一次 `doctor` 复测亦显示旧 IPC 套接字访问被执行环境拒绝、真实额度不可读。当前证据不足以证明外部工具能连接到桌面原会话使用的引擎并安全提交。

### 回归检查

执行 `python3 -m unittest discover -s tests -v`。测试报告计数 178，记有 2 项跳过、3 处错误。3 处均在执行环境拒绝绑定 Unix 或本地回环 socket 时出现。单独运行不依赖这些 socket 的测试，174 项通过、2 项跳过。

### 2026-11-23 隔离验证

- 当前读取与诊断修改通过 macOS、Ubuntu 和 Windows 回归。三个平台均收集 187 项 Python 测试，分别跳过 2、4、10 项，均无失败；各平台 26 项 Node 测试通过。[回归记录](https://github.com/progressrdx/codex-auto-resume/actions/runs/45019712768)
- 用桌面内置 CLI 对应的上游标签 `rust-v0.171.0-alpha.13` 编译引擎。在同一个引擎实例内，模拟服务先返回结构化额度错误，下一次调用接受请求。对原失败轮次执行一次空唤醒后收到完成回执，再次使用原轮次 ID 被拒绝。另两条原生测试验证活动轮次和已被取代的轮次受保护。[测试代码及实验记录](https://github.com/progressrdx/codex-auto-resume/pull/6) · [通过的运行](https://github.com/progressrdx/codex-auto-resume/actions/runs/45201457996)
- 空唤醒依赖原会话引擎内部的 `continue_turn_if_idle`。当前桌面对外协议尚无已验证的调用路径。尚未在安装版 App 上完成真实发送或真实账户的自然额度重置验证。

## 历史记录 · 2026-11-03

### 结论

已修复本次发现的字段兼容问题：v11 快照的可选 `ephemeral` 缺失值不再按
App/CLI 版本号处理，而是在核验本地原任务所有者和状态协议后归一化为 false。
显式 null/true、无效类型及其他必要安全状态缺失仍阻止发送。
最新正式版的静态协议检查和离线回归通过，尚未完成新版真机发送及自然重置验证。

### 官方版本与静态证据

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

### 实际执行的验证

1. 本机已安装 Mac `26.1023.50603 / build 22391`：运行当前工具 doctor，真实只读连接和额度查询成功。它不是上述最新 Mac 包，不能冒充最新版本真机验证。
2. 修复前，使用现有 ScriptedPipe 合成状态，分别注入上述最新 Windows/Mac 版本信息：缺失 `ephemeral` 时均停止，模拟续跑请求数为 0；明确提供 false 时均可被策略识别为允许续跑。
3. 修复后，同样省略该可选字段，两套最新版信息均通过完整模拟请求/响应，并分别只向原任务发出一次模拟续跑请求，获得模拟 turn ID。未来版本信息也走同一协议规则；Mac 还通过真实 Unix socket 与模拟 App 的完整交换。
4. 拒发回归覆盖显式 true/null/无效字段类型、缺失 hostId/resumeState/requests/历史、协议变化；原有审批、预算、取消和去重回归保留。
5. 上述为离线模拟。Windows CLI 版本未在 macOS 执行；未做最新版本真机发送或自然额度重置验证。

### 修复边界

字段兼容规则已按核验过的 v11 协议语义处理，无需每次更新添加 App 版本号。
此规则仅在接收来自原任务所有者的本机 v11 完整快照时生效，不应用于历史查询或未知协议。
不能把所有未知字段缺失都视为安全；
临时会话、审批状态、原任务核验、取消、预算、持久去重和不确定不重试仍须保留。
