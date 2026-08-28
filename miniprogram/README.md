# 续航 · 微信小程序

原生 WXML / WXSS / JavaScript，不是网页套壳。复用当前 Web 后端，手机不另存一套任务状态。

**当前未完成真机交付验收。** 已接入用户提供的 AppID `wx14176cf92cfb62b9`，微信模拟器原生界面和真实 `wx.request` 联调通过：模拟任务开启/停止，真实数据只读比对。手机扫码、可信 HTTPS 和正式域名校验尚未验证，不能以模拟器代替真机或自然额度恢复验收。详见 [验证摘要](../VALIDATION.md)。

## 功能

- 连接自己的后端；凭据只在当前小程序运行内存中，不存入代码、云端或本地存储。
- 读取同一组对话、额度与监控记录；前台每 15 秒刷新，切后台停轮询。
- 搜索标题、归档筛选、逐个识别长任务资格；切换/返回/后台会丢弃过期检查。
- 明确同意后开启托管，发送前重新检查；停止需确认，停止不撤回消息。
- POST 结果不确定时，持久保存操作意图，禁止自动重试。人工核对记录后才能解锁；服务端发送去重仍独立生效。
- 不提供任意提示词、自动批准、额度重置或新建业务线程入口。

## 真实运行前置条件

1. 微信开发者工具扫码登录，使用自己的小程序 AppID（无需 AppSecret）。本项目已配置用户提供的 AppID；其他使用者需替换为自己的。
2. 电脑上的 Codex App、续航 Web 服务与监控进程持续运行。
3. 手机能访问电脑的可信 HTTPS 地址。手机的 `127.0.0.1` 是手机自身。
4. 配置微信 request 合法域名、匹配域名的可信证书及完整证书链。局域网 IP 通信有微信单独规则，但不代表自签名证书可用于正式真机验收。

对于可解析到 Mac 的 HTTPS 域名，可以使用新增的唯一来源配置（地址和证书仅为占位示例）：

```sh
python3 -m codex_resume web --host 192.168.1.20 --port 8765 \
  --certfile /path/to/fullchain.pem --keyfile /path/to/key.pem \
  --public-origin https://relay.example.com:8765 \
  --mini-app-id wx14176cf92cfb62b9
```

后端仅接受这个精确 Host / Origin，仍需连接凭据，不开放跨站 CORS。配置该选项后不能再通过另一个 Host 访问同一监听器。未配置时保留原有 IP:端口行为。此命令不会配置 DNS、公网路由、证书、微信后台或防火墙；本次没有创建隧道，也没有公开真实任务服务。

`--mini-app-id` 处理开发者工具实际 `wx.request` 携带的 `Sec-Fetch-Site: same-site`（也兼容 cross-site）：只有 API 请求、有效连接凭据、无 Origin、精确 AppID 的微信 Referer 才能通过该例外。它不是免登录机制，也不会信任其他网站或允许跨站 CORS。修改启动参数需要重启 Web 服务并重新连接，不需要重启 watcher。

在微信开发者工具导入 `miniprogram/`，配置自己的 AppID，编译后填入服务地址和启动日志中的连接凭据。不要将凭据或 AppSecret 提交仓库。重启服务会轮换凭据，需要重新连接。

## 可复现验证

仓库根目录运行：

```sh
python3 -m unittest discover -s tests -v
npm ci --ignore-scripts
npm test
npm audit
```

自动化 SDK 是开发依赖，不进入小程序包；其旧传递依赖通过锁文件与 overrides 固定到修复版本。`npm audit` 当前为 0，不使用强制降级 SDK 的建议。

### 微信模拟器隔离测试

```sh
node scripts/prepare_miniprogram_dev.cjs
```

此命令输出一个临时项目目录。仅这个隔离副本允许开发环境回环 HTTP 请求，正式项目保持 `urlCheck: true`；不能用它证明正式域名/证书或真机已通过。用开发者工具导入并完成基础库加载后：

```sh
/Applications/wechatwebdevtools.app/Contents/MacOS/cli auto --project <上一步的临时目录> --auto-port 9420 --trust-project
npm run test:mobile-runtime
```

脚本用真正的 `wx.request` 连接临时回环后端，但任务数据完全模拟，**不会连接 8765 或操作真实任务**。只有全部断言通过才生成 `tests/evidence/miniprogram-runtime.json` 和截图。停止确认弹窗由测试自动确认；请求接口不做 mock。使用完关闭测试项目/自动化端口，不要把开发者工具控制端口开放到其他设备。

**本机 SDK 脚本仍未通过**：官方 SDK 能连接，但页面方法调用超时。2026-08-29 的通过证据来自原生界面逐步操作、真实微信请求与后端记录复核；停止弹窗也由原生界面操作，没有 mock。复现该路径可运行 `python3 tests/web_fixture.py --mini-app-id wx14176cf92cfb62b9 --trace`，在隔离模拟器中连接 `http://127.0.0.1:8876`、凭据 `ui-test-only`。此凭据和数据仅限模拟服务。`--trace` 不记录凭据或请求体。

项目配置保留 `cloudfunctionRoot: "cloudfunctions/"`，兼容本机工具 2.01.2510290 编译器对路径字段的要求；没有部署或启用云函数。正式源项目的 `urlCheck` 仍为 true。

### 真实后端只读比对

将 `RELAY_PRIVATE_LOG` 环境变量指向自己的私有启动日志，再运行 `node scripts/verify_mobile_readonly.cjs`；可设置 `RELAY_CHECK_THREAD` 为明确选择的任务编号，`RELAY_BASE_URL` 指定验证地址（默认 8765）。脚本不输出凭据、任务标题或正文，只报告一致性及条数。

此验证使用小程序实际客户端模块加 Node HTTP 适配器，不等同于微信运行时测试。所有操作均为 GET，不开启/停止监控。

## 验收还需要

- 手机真机连接成功，比较同一后端的对话、额度和监控数据。模拟器运行时已通过，见实测证据。
- 在隔离测试任务中用手机实际点选：检查 → 确认 → 开启 → 后端状态变化 → 停止 → 后端状态变化；错误凭据、断网、后台切换、重复点击验证。
- 真机安全域名/证书校验开启，检查小屏、横屏、大字体和读屏。
- 自动续跑本身仍需独立自然额度周期证据，不能由手机测试替代。

参考：[微信网络规则](https://developers.weixin.qq.com/miniprogram/dev/framework/ability/network.html)、[官方自动化 SDK](https://developers.weixin.qq.com/miniprogram/dev/devtools/auto/quick-start.html)。2026-08-28 读取官方页面核对网络约束与自动化流程。
