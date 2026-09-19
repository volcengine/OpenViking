# 快速开始

OpenViking 以服务端运行。用独立的 `ov` CLI 连接服务，导入一份小文档，再检索其中的内容。使用托管服务或他人部署的服务时，只需安装 CLI。

## 1. 选择服务

**已有服务地址？** 准备好 URL 和 API Key，直接跳到第 2 步。服务采用 API Key 认证时，数据访问使用 user/admin key，root key 用于管理操作。详见[认证](../guides/04-authentication.md)。

还没有服务时，选择以下一种方式：

### 火山引擎托管服务

打开 [OpenViking 控制台](https://console.volcengine.com/vikingdb/openviking/region:openviking+cn-beijing)，从**用户管理 → API Key** 获取密钥。服务地址为：

```text
https://api.vikingdb.cn-beijing.volces.com/openviking
```

无需安装服务端，也无需在本机配置模型。[产品介绍](https://www.volcengine.com/product/openviking-service)和[服务文档](https://docs.volcengine.com/docs/84313/2374478)说明托管服务的使用方式与额度。继续第 2 步。

### 自建服务

在运行服务端的机器上[安装 uv](https://docs.astral.sh/uv/getting-started/installation/)，再安装 OpenViking：

```bash
uv tool install openviking --upgrade
openviking-server init
openviking-server doctor
openviking-server
```

配置向导用于设置服务端模型并写入 `~/.openviking/ov.conf`。准备好 Embedding 模型和 VLM 的访问凭据——推荐使用火山引擎（豆包）模型，购买和开通见[火山引擎购买指南](../guides/02-volcengine-purchase-guide.md)——再用 `doctor` 检查配置。保持服务运行，另开终端完成后续步骤。

服务是否在运行，用一条 curl 即可确认，不依赖任何客户端：

```bash
curl http://127.0.0.1:1933/health
# {"status":"ok","healthy":true,...}
```

本地地址为 `http://127.0.0.1:1933`，默认本地配置不需要 API Key；Web Studio 位于 `/studio`。Docker、持久化存储和远程访问配置见[部署](../guides/03-deployment.md)、[模型配置](../guides/01-configuration.md)和[认证](../guides/04-authentication.md)。

## 2. 安装并连接 CLI

在客户端机器上安装好 Node.js 和 npm 后，运行：

```bash
npm install -g @openviking/cli
ov language zh-CN
ov config
```

在交互配置中，火山托管服务选择 **OpenViking Service**，自建服务选择 **自定义（Custom）**。填写 API Key，自建服务还需填写 URL。默认本地服务的密钥留空。保存并激活配置。

CLI 将当前连接保存到 `~/.openviking/ovcli.conf`，它与服务端的 `ov.conf` 是两个文件。脚本化配置和多服务切换见 [CLI 配置](05-cli-setup.md)。

检查连接：

```bash
ov health
```

这一步确认服务能响应；下面的导入还会验证模型处理和数据访问。

## 3. 导入文档

在当前目录创建 `quickstart.md`，内容如下：

```markdown
# Atlas 项目

Atlas 项目每周五备份文档。
Maya 负责备份流程，每份备份保留 30 天。
```

将它导入新的资源目录：

```bash
ov add-resource ./quickstart.md --to viking://resources/quickstart-demo --wait --timeout 120
```

CLI 会自动上传本地文件。`--wait` 等待处理完成，命令成功后再继续。若省略该参数，保存返回的 `task_id`，用 `ov task status <task_id>` 查询到 `completed` 后再使用结果。详见[后台任务](../api/17-tasks.md)。

本例使用尚未使用的目标 URI。重复运行示例时，换一个新目标，并同步替换下方命令中的 URI。

## 4. 浏览与检索

```bash
ov tree viking://resources/quickstart-demo
ov overview viking://resources/quickstart-demo
ov find "谁负责备份流程？" --uri viking://resources/quickstart-demo
```

`tree` 列出导入后的结构，`overview` 读取生成的概览，`find` 返回相关上下文的 URI 和分数。读取某条命中时，把返回的 URI 传给 `ov read`：

```bash
ov read "<returned-file-uri>"
```

将 `<returned-file-uri>` 替换为结果中的文件 URI，不保留尖括号。更多资源类型和检索参数见[资源管理](../api/02-resources.md)与[检索](../api/06-retrieval.md)。

## 使用 SDK

OpenViking 也提供 Python、TypeScript/JavaScript 和 Go SDK，均连接同一个服务端。客户端示例见 [API 概览](../api/01-overview.md)。
