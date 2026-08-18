## 步骤 1：安装 OpenViking 插件

在跑 OpenClaw 的那台机器上执行：

```bash
openclaw plugins install clawhub:@openviking/openclaw-plugin && openclaw openviking setup
```

## 步骤 2：把 setup 指到火山云

`openclaw openviking setup` 会问 Base URL 和 API Key，按下面填：

- Base URL：

```text
https://api.vikingdb.cn-beijing.volces.com/openviking
```

- API Key：把本页的 API Key 粘贴进去

也可以一次写完：

```bash
openclaw openviking setup --base-url https://api.vikingdb.cn-beijing.volces.com/openviking --api-key <本页的-API-Key>
```

## 步骤 3：重启 OpenClaw

```bash
openclaw gateway restart
```

重启后控制台会自动判断 Agent 接入状态。
