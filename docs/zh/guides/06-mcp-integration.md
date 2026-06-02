# MCP 集成指南

OpenViking 服务器内置 [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) 端点，任何兼容 MCP 的客户端都可以通过 HTTP 直接访问其记忆和资源能力，无需部署额外进程。

> **快速接入？** 见 [MCP 客户端](../agent-integrations/06-mcp-clients.md) 获取各平台配置片段和注意事项。本页面覆盖完整的工具参考和高级配置。

## 前提条件

1. 已安装 OpenViking（`pip install openviking` 或从源码安装）
2. 有效的配置文件（参见[配置指南](01-configuration.md)）
3. `openviking-server` 正在运行（参见[部署指南](03-deployment.md)）

MCP 端点位于 `http://<server>:1933/mcp`，与 REST API 同进程、同端口。

## 已验证的接入平台

以下平台已成功接入并使用 OpenViking MCP：

| 平台 | 接入方式 |
|------|----------|
| **Claude Code** | `type: http` 接入 |
| **Trae** | 标准 MCP 配置 |
| **Cursor** | 标准 MCP 配置 |
| **ChatGPT & Codex** | 标准 MCP 配置 |
| **OpenCode** | 标准 MCP 配置 |
| **Manus** | 标准 MCP 配置 |
| **Claude.ai / Claude Desktop** | 原生 OAuth 2.1（见 [11-oauth](11-oauth.md)） |

## 鉴权方式

MCP 端点的鉴权与 OpenViking REST API 完全一致，复用同一套 API-Key 认证系统。传入以下任一 header 即可：

- `X-Api-Key: <your-key>`
- `Authorization: Bearer <your-key>`

本地开发模式（服务器绑定 localhost）下无需认证。

## 客户端配置

### 通用 MCP 客户端

大多数支持 MCP 的平台（如 Trae、Manus、Cursor 等）使用标准的 `mcpServers` 配置格式：

```json
{
  "mcpServers": {
    "openviking": {
      "url": "https://your-server.com/mcp",
      "headers": {
        "Authorization": "Bearer your-api-key-here"
      }
    }
  }
}
```

### Claude Code

Claude Code 需要额外指定 `"type": "http"`。可通过命令行添加：

```bash
claude mcp add --transport http openviking \
  https://your-server.com/mcp \
  --header "Authorization: Bearer your-api-key-here"
```

或在 `.mcp.json` 中手动配置：

```json
{
  "mcpServers": {
    "openviking": {
      "type": "http",
      "url": "https://your-server.com/mcp",
      "headers": {
        "Authorization": "Bearer your-api-key-here"
      }
    }
  }
}
```

加 `--scope user` 可将配置设为全局（所有项目共享）。

### Claude.ai / Claude Desktop（OAuth）

这些客户端只接受 OAuth 2.1，不接受 API Key。OpenViking 已经原生实现 OAuth 2.1（DCR + PKCE + opaque token，SQLite 后端，配合 console 驱动的 OTP 授权页），不再需要外部代理。

如果你已经为 OpenViking 服务配好了 HTTPS，直接连接 `https://your-server.com/mcp` 端点即可——客户端会自动引导完成 OAuth 授权流程。

**详见 [OAuth 2.1 接入指南](11-oauth.md)** 和 **[公网访问指南](12-public-access.md)**：

- 端到端流程（device-flow 风格：authorize 页显示 6 字符码，用户在 console 确认）
- HTTP（本地）与 HTTPS（生产）两阶段部署，包含 Caddy / nginx 反代模板和 docker-compose 示例
- Claude.ai / Claude Desktop 接入步骤
- `OPENVIKING_PUBLIC_BASE_URL` 与 `oauth` 配置项
- Token 模型（`ovat_` / `ovrt_` / `ovac_` 前缀）与撤销

> 社区项目 [MCP-Key2OAuth](https://github.com/t0saki/MCP-Key2OAuth) Cloudflare Worker 代理仍可作为第三方备选方案，但现在更推荐原生流程：无需额外部署单元，也不会引入第三方对 API Key 的信任面。


## 可用的 MCP 工具

连接后，OpenViking MCP 端点暴露 14 个工具：

| 工具 | 说明 | 主要参数 |
|------|------|----------|
| `search` | 语义搜索记忆、资源和技能 | `query`, `target_uri`(可选), `limit`, `min_score` |
| `read` | 读取一个或多个 `viking://` URI 的内容 | `uris`（单个字符串或数组） |
| `list` | 列出 `viking://` 目录下的条目 | `uri`, `recursive`(可选) |
| `store` | 存储消息到长期记忆（触发记忆提取） | `messages`（`{role, content}` 列表） |
| `add_resource` | 添加本地文件或 URL 作为资源(本地文件触发渐进式上传流) | `path`, `temp_file_id`(可选), `description`(可选), `watch_interval`(可选,分钟数 — 远程 URL 的自动刷新周期), `to`(可选,目标 `viking://resources/...` URI；`watch_interval > 0` 时若省略 `to`,watch 将自动绑定到本次 add 创建的资源 URI) |
| `list_watches` | 列出当前 Agent 可见的 watch 任务（自动刷新订阅），每行显示目标 URI、刷新间隔（分钟）、active/paused 状态以及下一次调度时间 | 无 |
| `cancel_watch` | 按目标 URI 取消（删除）watch 任务。若需调整刷新周期或临时暂停，请取消后使用新的 `watch_interval` 重新添加 | `to_uri`（必须匹配 watch 任务的 `to` 值，例如 `viking://resources/...`） |
| `grep` | 在 `viking://` 文件中进行正则内容搜索 | `uri`, `pattern`（字符串）, `case_insensitive` |
| `glob` | 按 glob 模式匹配文件 | `pattern`, `uri`(可选范围) |
| `forget` | 删除任意 `viking://` URI（先用 `search` 查找；删除目录需 `recursive=true`） | `uri`, `recursive`(可选) |
| `code_outline` | 显示文件的符号结构（类、函数、方法及其行号范围），不读取实现体。在决定 `read` 之前用于快速浏览文件。 | `uri`（必须是 `viking://` **文件** URI） |
| `code_search` | 在 `viking://` 目录下按子串搜索符号名（类 / 函数 / 方法），返回符号类型、所属类、文件 URI、行号范围。最多扫描 200 个源文件。 | `query`, `uri`（必须是 `viking://` 目录；缩小到子目录可获得更深覆盖） |
| `code_expand` | 返回单个命名符号的完整源码，避免读取整个文件。 | `uri`（文件）, `symbol`（`bar` 表示顶层，`Foo.bar` 表示方法） |
| `health` | 检查 OpenViking 服务健康状态 | 无 |

> **注**：MCP 仅暴露 watch 管理的最小闭包（`list_watches` + `cancel_watch`）。pause / resume / trigger 和统一的 `update` 动作刻意不在此处暴露，请通过 REST `/api/v1/watches/*` 接口或 `ov task watch` CLI 使用上述操作。

### 添加本地文件资源(渐进式上传)

`add_resource` 工具同时接受**远程 URL** 和**本地文件路径**。两者的处理路径不同:

- **远程 URL**(`http(s)://`、`git@`、`ssh://`、`git://`):一次调用即完成,server 直接拉取并入库。
- **本地文件路径**:返回**两步上传指令**(纯文本,Step 1 / Step 2 排版),agent 需要:
  1. 把文件以 `multipart/form-data` POST 到响应里给出的 `temp_upload_signed` URL(URL 内嵌一次性 token,默认 10 分钟过期)。Server 在写入时 mint `temp_file_id`,通过 JSON `{"temp_file_id": "..."}` 返回。
  2. 从响应体读出 `temp_file_id`,再次调用 `add_resource(temp_file_id="<step 1 返回的 id>")`,server 通过 `TempUploadStore` 解析文件并入库。

这样设计是为了让任何 MCP 客户端(包括无本地文件系统的 Claude web、Manus 等沙箱环境)都能往 OpenViking 灌文件,而不需要客户端预装 `ov` CLI。签名端点和认证版的 `temp_upload` 共享同一个持久化层(`TempUploadStore`),所以 `local` / `shared` 上传模式(以及 `shared` 模式带来的多 worker 支持)在两个端点上行为一致。

#### 必须配置 `OPENVIKING_PUBLIC_BASE_URL` 的场景

工具响应里给出的上传 URL,server 端按以下顺序解析:

1. 环境变量 `OPENVIKING_PUBLIC_BASE_URL`
2. `ov.conf` 中的 `server.public_base_url`
3. 请求头 `X-Forwarded-Host` / `X-Forwarded-Proto`(由反代链转发)
4. 请求头 `Host`(直连场景)
5. 监听地址兜底 `http://{host}:{port}`

只要 server 部署在反向代理(nginx / cloud LB / k8s ingress)后,**强烈建议显式配置 `OPENVIKING_PUBLIC_BASE_URL`**。后两层是兜底推断,在以下情况会失败:

- 反代/MCP proxy 不转发 `X-Forwarded-*` 头
- server 监听 `0.0.0.0`(fallback URL 含 `0.0.0.0`,agent 无法连接)
- 多层代理存在 host 重写

未配置该变量且 fallback 推断生效时,工具响应末尾会自动附带提示,告知用户在 server 端设置该环境变量。Docker Compose 部署示例:

```yaml
services:
  openviking:
    image: ghcr.io/volcengine/openviking:latest
    environment:
      OPENVIKING_PUBLIC_BASE_URL: "https://ov.your-domain.com"
```

## 故障排除

### 连接被拒绝

**可能原因：** `openviking-server` 未运行，或运行在不同端口上。

**解决方案：** 验证服务器是否正在运行：

```bash
curl http://localhost:1933/health
# 预期返回：{"status": "ok"}
```

### 认证错误

**可能原因：** 客户端配置与服务器配置中的 API 密钥不匹配。

**解决方案：** 确保 MCP 客户端配置中的 API 密钥与 OpenViking 服务器配置中的一致。参见[认证指南](04-authentication.md)。

## 参考

- [MCP 规范](https://modelcontextprotocol.io/)
- [OpenViking 配置](01-configuration.md)
- [OpenViking 部署](03-deployment.md)
