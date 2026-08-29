# Codex Auto Resume

这是一个纯本地源码工具：监控一个由用户明确选择的 Codex 桌面 App 任务，在该任务因额度耗尽暂停后等待额度自然恢复，并在原任务中继续。

它不会切换账号、购买或重置额度、自动批准操作，也不会扫描并接管所有任务。当前版本只包含已经确认的 Python 核心、命令行入口和本机 Web 管理界面，不包含原生 macOS 客户端、微信小程序、云服务器或移动端代码。

## 使用前准备

- macOS
- Python 3.9 或更高版本
- 已安装、登录并保持打开的 Codex 桌面 App
- 当前验证的 App 版本：`26.820.60940 / build 7119`，Bundle ID 为 `com.openai.codex`

不需要安装 pip、npm 或其他第三方依赖，也不需要域名、云服务器或 Apple 开发者证书。

## 下载与启动

在终端运行：

```sh
git clone https://github.com/progressrdx/codex-auto-resume.git
cd codex-auto-resume
./resume doctor
./resume web
```

`doctor` 只检查 Codex App 连接、兼容版本和账户额度，不会启动或续跑任务。

`web` 会在终端显示两项内容：

```text
控制台地址：http://127.0.0.1:8765/
连接凭据：一段每次启动都会变化的随机文字
```

用本机浏览器打开控制台地址，把连接凭据复制到页面中即可。保持运行 `./resume web` 的终端窗口打开；关闭网页不会停止已经启动的后台监控。

## 在界面中使用

1. 输入终端显示的连接凭据。
2. 查看五小时和每周额度。
3. 点击“托管长任务”，从本地对话列表选择一个任务。
4. 工具会先只读检查任务状态；只有正在执行或明确因额度暂停的任务才能托管。
5. 确认累计续跑上限，默认最多 3 次，然后点击“加入托管”。
6. 需要取消时，在监控卡片中点击“停止监控”。停止只取消未来续跑，不会打断 Codex App 当前正在执行的工作。

每次只管理用户明确选择的任务。标题搜索只帮助选择，真正操作前仍会用精确的任务 UUID 和实时状态复核。

## 常用配置

更换本机端口：

```sh
./resume web --port 9000
```

Codex App 不在默认位置时：

```sh
./resume --app "/Applications/你的 Codex App.app" web
```

使用指定 Python：

```sh
PYTHON_BIN=/path/to/python3 ./resume web
```

使用其他 Codex 数据目录或工具状态目录：

```sh
./resume --home /path/to/.codex --state-dir /path/to/state web
```

控制台固定只监听 `127.0.0.1`，不能从其他设备或公网访问。连接凭据只保存在当前浏览器标签页，服务重启后自动失效。

## 命令行方式

不使用 Web 界面时，也可以运行：

```sh
./resume list
./resume check 任务UUID
./resume start 任务UUID
./resume status
./resume stop 任务UUID
```

`check` 是只读检查。`start` 默认累计最多尝试 3 次；明确需要其他上限时可使用 `--max-resumes 5`。出现多个额度桶时必须通过 `--limit-id` 明确指定，工具不会猜测。

## 更新源码

先在界面或命令行停止需要升级的监控，然后运行：

```sh
git pull
./resume doctor
./resume web
```

状态、预算和防重复记录保存在 `~/.codex-auto-resume/`，更新源码不会清空这些记录。服务重启会生成新的连接凭据。

## 安全边界与限制

- 只会在有结构化 `UsageLimitExceeded` 证据时等待恢复；正常结束、手动停止、审批、等待输入和其他错误不会自动续跑。
- 发送前会重新读取任务状态、额度、模型、权限、工作目录和任务目标；发送结果不确定时停止，禁止自动重试。
- 自动续跑使用固定提示，不接受网页传入任意提示词，也不处理审批。
- Codex App 必须保持运行。电脑关机或重启后需要重新启动工具；睡眠唤醒后会重新核验。
- 当前尚未完成“真实额度耗尽 → 跨自然重置窗口 → 自动续跑”的完整端到端验证，因此仍是试用工具，不保证无人值守完成任务。
- App 内部接口可能变化；遇到未验证版本时工具会停止，而不会放宽版本保护继续操作。

本地状态目录只保存任务 UUID、状态摘要、次数、去重标识和简短日志，不保存完整会话、源码、账号令牌或原始查询错误。

## Codex 仓库技能

仓库包含 `.agents/skills/codex-auto-resume/`。从本仓库打开 Codex 后，可以使用 `$codex-auto-resume` 检查或管理一个明确选择的任务。该技能调用同一个 `./resume` 入口，不会自动选择任务或替用户授权开启监控。

## 开发验证

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q codex_resume
node --test tests/test_ui.cjs
node --check codex_resume/static/app.js
python3 scripts/verify_release.py
```

测试只使用内存消息、回环 HTTP 服务和临时数据库，不访问真实账户、不修改 Codex App，也不会启动真实任务。
