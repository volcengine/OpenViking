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
| **OpenCode** | OpenCode 原生 `mcp` 配置 |
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

### OpenCode

在 `~/.config/opencode/opencode.json` 中配置：

```json
{
  "mcp": {
    "openviking": {
      "type": "remote",
      "url": "https://your-server.com/mcp",
      "enabled": true,
      "oauth": false,
      "headers": {
        "Authorization": "Bearer your-api-key-here"
      }
    }
  }
}
```

### Claude.ai / Claude Desktop（OAuth）

这些客户端只接受 OAuth 2.1，不接受 API Key。OpenViking 已经原生实现 OAuth 2.1（DCR + PKCE + opaque token，SQLite 后端，配合 Studio consent 授权页），不再需要外部代理。

如果你已经为 OpenViking 服务配好了 HTTPS，直接连接 `https://your-server.com/mcp` 端点即可——客户端会自动引导完成 OAuth 授权流程。

**详见 [OAuth 2.1 接入指南](11-oauth.md)** 和 **[公网访问指南](12-public-access.md)**：

- 端到端流程（device-flow 风格：authorize 页显示 6 字符码，用户在 console 确认）
- HTTP（本地）与 HTTPS（生产）两阶段部署，包含 Caddy / nginx 反代模板和 docker-compose 示例
- Claude.ai / Claude Desktop 接入步骤
- `OPENVIKING_PUBLIC_BASE_URL` 与 `oauth` 配置项
- Token 模型（`ovat_` / `ovrt_` / `ovac_` 前缀）与撤销

> 社区项目 [MCP-Key2OAuth](https://github.com/t0saki/MCP-Key2OAuth) Cloudflare Worker 代理仍可作为第三方备选方案，但现在更推荐原生流程：无需额外部署单元，也不会引入第三方对 API Key 的信任面。


## 可用的 MCP 工具

连接后，OpenViking MCP 端点暴露 16 个工具：

| 工具 | 说明 | 主要参数 |
|------|------|----------|
| `find` | 无 session 上下文的快速语义检索 | `query`, `target_uri`(可选), `limit`, `min_score`, `level`(可选), `context_type`(可选) |
| `search` | 支持可选 session 上下文和意图分析的深度语义检索 | `query`, `target_uri`(可选), `session_id`(可选), `limit`, `min_score`, `level`(可选), `context_type`(可选) |
| `recall` | 按记忆类别配额召回，服务端组装为带 URI 的扁平 `<memory>` 块 | `query`, `quotas`(可选), `max_chars`, `min_score`, `peer_scope`, `other_peer_penalty`(可选), `session_id`(可选), `detail`(可选), `max_tokens`(可选), `rewrite`(可选) |
| `read` | 读取一个或多个 `viking://` URI 的内容 | `uris`（单个字符串或数组） |
| `list` | 列出 `viking://` 目录下的条目 | `uri`, `recursive`(可选) |
| `tree` | 以缩进形式展示 `viking://` URI 下的递归目录树——当需要全面了解文件树结构时使用（单层列表用 `list`，按文件名查找用 `glob`） | `uri`(可选), `level_limit`(默认 3), `node_limit`(默认 1000), `include_abstract`(可选——同时展示每个文件的摘要) |
| `remember` | 存储消息到长期记忆（触发记忆提取） | `messages`（`{role, content}` 列表） |
| `write` | 向 `viking://` 文件写入文本（创建/覆盖/追加）。自动创建缺失的父目录；覆盖前请先用 `read` 查看当前内容；只改文件局部时优先用 `edit` | `uri`, `content`, `mode`(可选:默认 `replace` — 文件不存在时自动创建,`append` 追加,`create` — 已存在则失败), `wait`(可选,阻塞直到重建索引完成), `timeout`(可选) |
| `edit` | 在已有 `viking://` 文件中把精确字符串替换为新文本——用于局部修改，避免整文件重写。若 `old_string` 找不到、或匹配多处且 `replace_all` 为 false，则编辑失败且文件保持不变 | `uri`, `old_string`, `new_string`, `replace_all`(可选), `wait`(可选,阻塞直到重建索引完成), `timeout`(可选) |
| `add_resource` | 添加本地文件或 URL 作为资源(本地文件触发渐进式上传流) | `path`, `temp_file_id`(可选), `description`(可选), `watch_interval`(可选,分钟数 — 远程 URL 的自动刷新周期), `processing_mode`(可选：默认 `semantic_and_vectors`；传 `vectors_only` 时跳过 VLM 语义理解，只向量化当前文件), `to`(可选,目标 `viking://resources/...` URI；`watch_interval > 0` 时若省略 `to`,watch 将自动绑定到本次 add 创建的资源 URI), `args`(可选,特定 parser 参数，包括 `{"parse_mode":"no_split"}` 用于正常解析但每个源文档只生成一个 Markdown 正文、飞书一次性用户 token 导入使用 `{"feishu_access_token":"u-..."}`，或飞书用户 token watch 使用 `{"feishu_access_token":"u-...","feishu_refresh_token":"r-..."}`) |
| `list_watches` | 列出当前 Agent 可见的 watch 任务（自动刷新订阅），每行显示目标 URI、刷新间隔（分钟）、active/paused 状态以及下一次调度时间 | 无 |
| `cancel_watch` | 按目标 URI 取消（删除）watch 任务。若需调整刷新周期或临时暂停，请取消后使用新的 `watch_interval` 重新添加 | `to_uri`（必须匹配 watch 任务的 `to` 值，例如 `viking://resources/...`） |
| `grep` | 在 `viking://` 文件中进行正则内容搜索 | `uri`, `pattern`（字符串或数组）, `case_insensitive`, `node_limit` |
| `glob` | 按 glob 模式匹配文件 | `pattern`, `uri`(可选范围), `node_limit` |
| `forget` | 删除任意 `viking://` URI（先用 `search` 查找；删除目录需 `recursive=true`） | `uri`, `recursive`(可选) |
| `health` | 检查 OpenViking 服务健康状态 | 无 |

在 MCP 工具中，`viking://user` 表示当前认证用户的工作区。例如，
`viking://user/notes/todo.md` 会解析成
`viking://user/<当前用户>/notes/todo.md`，不依赖文件名或扩展名判断。工具返回的、
包含当前用户 ID 的 canonical URI 也可以直接使用；这套简写不用于跨用户访问。

> **注**：MCP 仅暴露 watch 管理的最小闭包（`list_watches` + `cancel_watch`）。pause / resume / trigger 和统一的 `update` 动作刻意不在此处暴露，请通过 REST `/api/v1/watches/*` 接口或 `ov task watch` CLI 使用上述操作。

> 未传 `args.feishu_access_token` 的飞书/Lark 导入保持现有应用/tenant token 行为，也支持 watch。飞书/Lark 一次性用户 token 导入只传 `args.feishu_access_token`；飞书/Lark 用户 token watch 还必须传 `args.feishu_refresh_token`，并要求 OpenViking 服务端配置同一个飞书应用凭证。

> `processing_mode=vectors_only` 会跳过 VLM 语义理解阶段，不生成或刷新 `.abstract.md` / `.overview.md`；它只向量化当前非隐藏资源文件，并保留已存在的旧语义产物。

### 添加本地文件资源(单步上传)

`add_resource` 工具同时接受**远程 URL** 和**本地文件路径**。两者的处理路径不同:

- **远程 URL**(`http(s)://`、`git@`、`ssh://`、`git://`):一次调用即完成,server 直接拉取并入库。
- **本地文件路径**:返回**上传指令**(纯文本)。agent 把文件以 `multipart/form-data`(字段名 `file`)POST 到响应里给出的 `temp_upload` URL。该 URL 内嵌一次性 token(默认 10 分钟过期)作为鉴权凭证,无需 API Key。Server 随后在**同一次请求内自动入库**并返回最终结果,agent **无需**再次调用 `add_resource`。

这样设计是为了让任何 MCP 客户端(包括无本地文件系统的 Claude web、Manus 等沙箱环境)都能往 OpenViking 灌文件,而不需要客户端预装 `ov` CLI。token 上传复用认证版的 `temp_upload` 路由(API Key 优先,否则走一次性 `?token=`)及其 `TempUploadStore` 持久化,所以 `local` / `shared` 上传模式行为一致。注意:一次性 token 保存在进程内,因此多 worker 部署下 `add_resource` 调用与后续的上传 POST 必须落到同一个 worker(或以单 worker 运行),token 才能被解析。

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
