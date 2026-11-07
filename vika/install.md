# 安装 OpenViking SuperAgent

本页带你完成首次安装和收发验证。功能介绍见 [README](README.md)，高级配置见 [配置与运维参考](docs/installation-reference.md)。CLI 命令名为 `vika`。

## 准备

- **系统：** macOS 或 Linux，x64 / arm64；Windows 使用 WSL2。不支持 musl / Alpine。需要 sh、curl、tar 和 sha256sum（或 shasum），不用预装 Node、npm、pm2 或 lark-cli。
- **OpenViking：** 必须连接一个可用的 OV 服务，可以在本机，也可以在远端。准备地址与服务要求的凭据，setup 中填写或导入配置；没有跳过选项。
- **运行时：** 选择下表中的一种，并准备好模型登录或 API 配置。
- **聊天账号：** 飞书需手机号注册的账号或企业账号，豆包账号登录的飞书不支持此接入。其他渠道按向导准备 Bot / App 凭据。

| 运行时 | 安装前准备 |
|---|---|
| Codex | 安装 Codex CLI 并登录 |
| Claude Code | 安装并登录，版本至少 2.1.259 |
| TraeX | 安装并登录支持 `app-server` 的版本 |
| Pi / Durable | 随包提供；复用 `~/.pi/agent` 的模型配置。没有配置时，安装后运行 `vika pi`，通过 `/login` 登录 |
| OpenCode | 安装并登录 1.15.7+ 的 **1.x**；本 release 暂不支持 2.x |

具体模型、版本探测与环境变量见 [运行时配置](docs/installation-reference.md#准备)。API key 不要写进 `config.json` 或提交到仓库。

## 安装

```bash
curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/vika/install.sh | sh
```

安装器校验下载包后安装到 `~/.vika/releases/`，首次在终端安装会进入配置向导。若当前终端找不到 `vika`，重新打开终端，或先用 `~/.vika/bin/vika`。

```bash
vika setup
```

跟随向导完成：

1. **连接 OV。** 选择已检测的配置，或填写本机 / 远端地址和凭据，验证通过后继续。
2. **配置 Agent。** 填名字、工作目录，选择可用的运行时。首个实例 ID 为 `primary`。
3. **接入聊天。** 选择渠道并授权；接多个聊天 IM 时，选择接收主动通知的主渠道。邮箱必须搭配一个聊天 IM。
4. **确认收发。** 保存后服务启动；飞书机器人会主动打招呼，再向它回复一条消息，确认消息能进来。

飞书可扫码新建 / 选择应用，或手动填写 App ID 与 App Secret。使用已有应用时必须采用长连接，且不能同时被另一套机器人服务监听。向导会询问是否允许以本人身份读取消息，授权范围见 [消息访问权限](docs/usage-reference.md#机器人能读到哪些消息)。

其他渠道的准备步骤：[微信](docs/installation-reference.md#微信扫码与单聊链路) · [QQ](docs/installation-reference.md#qq-机器人接入) · [Telegram](docs/installation-reference.md#telegram-bot-接入) · [Slack](docs/installation-reference.md#slack-接入)。

## 验证与日常管理

```bash
vika status
vika logs primary
vika restart primary
```

打开 `http://127.0.0.1:3737` 查看活动，再在机器人私聊里发一个明确任务，核对回复与工作区结果。只看到进程在线不等于消息链路已经打通。

- 配置其他实例、修改渠道或删除实例：`vika setup`。
- 登录过期：`vika login <渠道> [实例]`。
- 升级已发布版本：`vika upgrade primary`。
- 开机自启：`vika autostart enable`；Linux 退出登录后仍要运行，需设置 `loginctl enable-linger`。
- 切换界面语言：`vika --lang zh` 或 `vika --lang en`。

配置默认保存在 `~/.vika/config.json`；自定义路径需对相关命令统一设置绝对路径 `VIKA_CONFIG`。源码 checkout 的修改不会更新已安装的服务。

## 遇到问题

| 现象 | 先检查 |
|---|---|
| OV 验证失败 | 地址、网络与身份凭据；内网服务需从运行 Vika 的机器可达 |
| 没有可用运行时 | 对应 CLI 是否已登录，或用 `vika pi` 配置内置模型 |
| 飞书收不到消息 | 长连接、消息事件、应用发布 / 审核、机器人是否被其他服务同时监听 |
| 能收消息但工具报权限错误 | 应用权限及用户授权；[手动配置飞书](docs/installation-reference.md#手动配飞书不用-setup-时) |
| 启动或附件操作失败 | `vika logs primary`；[附件临时目录](docs/installation-reference.md#飞书图片文件收发的临时目录) |

[源码开发](docs/installation-reference.md#从源码运行开发) · [升级与回滚](docs/installation-reference.md#升级与回滚) · [多实例与记忆配置](docs/installation-reference.md#多个-agent) · [发布流程](../docs/repository/RELEASE_CN.md)
