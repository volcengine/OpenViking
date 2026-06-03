## 步骤 1：安装 OpenViking
在你的 OpenClaw 的机器终端，执行以下命令以安装 OpenViking Plugin

```bash
openclaw plugins install clawhub:@openviking/openclaw-plugin && openclaw openviking setup
```

## 步骤 2：输入以下信息
执行安装命令后会依次提示输入以下信息，可复制后粘贴到你的 Agent 终端

- Base URL: `https://api.vikingdb.cn-beijing.volces.com/openviking`
- API Key: 复制页面中展示的 API Key 到你的 Agent 终端

## 步骤 3：重启 OpenClaw
复制以下指令到 Agent 终端以重启 OpenClaw，重启后控制台将自动判断 Agent 接入状态

```bash
openclaw gateway restart
```
