# 发布计划

## 当前决策

首发改为 **GitHub 源码拉取 + 本机手动启动**。核心发布物是可审查的 Python 源码、根目录 `./resume` 入口和仓库级 Codex Skill；不分发需要 Apple Developer ID 的 `.app`，不要求域名、云服务器、数据库或本工具账户。

原生 SwiftUI 壳、微信小程序和移动端中继继续保留为后续实验，但不作为首发阻塞项。本阶段不购买 Apple Developer Program、域名或云基础设施。

## 源码首发范围

### 必须交付

- 用户拉取仓库后可用 `./resume` 启动，无需安装第三方 Python 包。
- 明确报告 macOS、Python 3.9+、受支持 Codex App 和 App 保持运行等前置条件。
- 保留 `doctor`、`list`、`check`、`start`、`status`、`stop` 和本机 Web 控制台。
- 只管理用户明确选择的一个任务；标题、最近任务或模糊文本不能代替 UUID。
- 保留发送前实时复核、落盘去重、次数预算、取消及不确定结果禁止重试。
- 仓库级 Skill 只能调用同一个安全入口；它不自动授权开启托管，也不承担后台常驻。
- 状态继续保存在用户私有的 `~/.codex-auto-resume/`，`git pull` 不清除历史预算或防重复记录。

### 不在首发范围

- 签名、公证的 macOS 安装包和登录启动项。
- 微信小程序正式交付、手机真机验收和跨网络控制。
- 公网服务、域名、ICP备案、云端中继、用户账户、设备配对和推送通知。
- 原生 iOS/Android 客户端。

## 当前进度（2026-08-29）

- 已完成核心 CLI、watcher、本地 Web 控制台和持久化安全边界。
- 已完成根目录 `./resume` 启动入口，并验证从仓库外工作目录调用。
- 已完成 `.agents/skills/codex-auto-resume/` 仓库级 Skill 及结构校验。
- 原生 SwiftUI、PyInstaller 和签名脚本作为实验代码保留，不进入源码首发路径。
- 尚未完成真实额度耗尽跨自然重置窗口的端到端自动续跑；此限制与发布方式无关。

## 发布验收

1. 在仅具备系统 Python 3.9+ 的环境克隆仓库并运行 `./resume --help` 与 `./resume doctor`。
2. 从仓库外目录调用启动器，确认模块定位不依赖当前工作目录。
3. 运行 `python3 -m unittest discover -s tests -v`，包含启动器和 Skill 结构测试。
4. 运行 `node --test tests/test_ui.cjs` 与 `node --check codex_resume/static/app.js`。
5. 运行 Python 编译检查和 `python3 scripts/verify_release.py`。
6. 在隔离、无副作用测试任务验证：只读诊断 → 明确选择 → 检查 → 开启 → 状态持久化 → 停止。
7. 真实业务任务不得用于试发；真实发送验证仍需明确授权和无副作用测试任务。

## 后续可选阶段

只有源码版本稳定并积累自然额度重置证据后，才重新评估安装包或插件目录分发：

- macOS 二进制需要 Developer ID Application、Hardened Runtime 和 Apple 公证。
- Codex Plugin 可用于更方便地分发 Skill，但实际 watcher 仍必须是用户 Mac 上的独立进程。
- 移动端若重启，应另行评审域名、服务器、隐私、账户、数据删除和安全响应责任。
