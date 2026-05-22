# OpenViking CLI 配置指南

本文介绍如何安装 OpenViking CLI、完成配置，并验证它可以连接到 OpenViking。

`ov` 会连接到一个已经存在的 OpenViking 服务端。它不是服务端安装命令。如果你还没有安装或启动 OpenViking 服务端，请先阅读[快速开始](02-quickstart.md)或[服务端模式](03-quickstart-server.md)。

## 本文配置什么

CLI 使用 `~/.openviking/ovcli.conf` 保存客户端连接配置。

使用 `ov config` 可以交互式创建或管理这个文件。它可以新增、编辑、删除已保存配置，并在校验成功后把某个配置设为 active。

## 最短路径

```bash
npm i -g @openviking/cli
ov --help
ov config
ov config validate
ov health
ov status
```

预期结果：

- `ov --help` 输出 OpenViking 命令列表。
- `ov config validate` 确认 active 配置可以连接服务端并通过鉴权。
- `ov health` 确认服务端基础可达。
- `ov status` 展示 active 配置和服务端诊断信息。

## 开始前

你需要准备：

- Node.js 和 npm。
- 一个可访问的 OpenViking 目标：
  - Volcengine Cloud，或
  - 自托管 OpenViking 服务端。
- 如果目标需要鉴权，需要准备 API Key。

API Key 是敏感凭证。建议通过 `ov config` 的交互式输入框填写。用于演示或试用时，建议使用之后可以撤销的临时 key。

## 安装 `ov`

先检查是否已经安装：

```bash
command -v ov
ov --version
```

安装或升级 npm 包：

```bash
npm i -g @openviking/cli
```

验证：

```bash
ov --help
```

如果仍然找不到 `ov`，关闭并重新打开 shell，或检查 npm 全局 bin 目录：

```bash
npm bin -g
```

## 选择连接目标

### Volcengine Cloud

如果你希望使用火山引擎托管的 OpenViking，选择此项。

- 服务端 URL 固定为：`https://api.vikingdb.cn-beijing.volces.com/openviking`
- API Key 必填。
- API Key 获取地址：https://console.volcengine.com/vikingdb/openviking/region:openviking+cn-beijing

### Self-Managed

如果你运行或维护自己的 OpenViking 服务端，选择此项。

- 本地默认 URL：`http://127.0.0.1:1933`
- 本地无鉴权服务通常不需要 API Key。
- 远程或开启鉴权的自托管服务端可能需要 API Key。

## 配置 CLI

运行：

```bash
ov config
```

然后选择：

1. `Add config`
2. `Volcengine Cloud` 或 `Self-Managed`
3. 配置名称，或留空自动生成
4. 所需的 URL / API Key
5. 校验成功后保存配置

如果你维护多个 OpenViking 目标，之后可以使用：

```bash
ov config switch
```

切换 active 配置。

## 验证

配置完成后运行：

```bash
ov config show
ov config validate
ov health
ov status
```

检查配置时优先使用 `ov config show`，因为它会隐藏密钥。

除非你理解配置文件可能包含密钥，否则不要打印原始配置文件。

## 凭证安全

- API Key 可能允许访问你的 OpenViking 数据。
- 建议通过 `ov config` 的交互式输入框填写 API Key。
- 不要把 API Key 直接写进 shell 命令。
- 不要让 API Key 留在 shell history。
- 不要打印原始 `~/.openviking/ovcli.conf`。
- 不要分享包含 API Key 的截图。
- 演示和试用建议使用临时或可撤销的 key。

## 常见问题

### 找不到 `ov`

运行：

```bash
npm i -g @openviking/cli
npm bin -g
```

然后重新打开 shell，或把 npm 全局 bin 目录加入 `PATH`。

### npm 全局安装失败

如果 npm 报权限错误，请按你平时管理 Node.js 的方式处理。除非你本来就用 sudo 管理全局 npm 包，否则不要直接运行 `sudo npm i -g`。

### 本地服务端没有运行

对于本地 Self-Managed 配置，先验证服务端：

```bash
curl http://127.0.0.1:1933/health
```

如果失败，先启动服务端再配置 `ov`。参见[服务端模式](03-quickstart-server.md)。

### API Key 校验失败

重新运行 `ov config` 并编辑配置。对于 Volcengine Cloud，确认 API Key 来自上面的 OpenViking 控制台地址。对于自托管服务，确认服务端是否要求鉴权。

### active 配置不对

检查并切换：

```bash
ov config show
ov config switch
ov config validate
```

### 旧配置命令

使用 `ov config`。不要使用旧的或已移除的配置命令，例如 `ov config setup-cli`。

## 下一步

CLI 配置完成后，可以尝试：

```bash
ov add-resource https://github.com/volcengine/OpenViking --wait
ov find "what is OpenViking"
ov tree viking://resources/ -L 2
```

查看全部命令：

```bash
ov --help
ov <command> --help
```
