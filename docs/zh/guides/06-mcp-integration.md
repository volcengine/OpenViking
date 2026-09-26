# MCP 集成指南

OpenViking Server 内置 [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) 端点。支持 Streamable HTTP 的客户端可直接连接；只支持 stdio 的客户端可使用 [Agent Plugins 包](../agent-integrations/15-agent-plugins.md)提供的代理。

> **快速接入？** 见 [MCP 客户端](../agent-integrations/06-mcp-clients.md) 获取各平台配置片段和注意事项。本页面覆盖完整的工具参考和高级配置。

## 前提条件

准备可访问的 OpenViking 服务地址和对应凭证。需要自行部署时，先完成[快速开始](../getting-started/02-quickstart.md)；使用托管服务或已有部署时，无需在本地安装服务端。

MCP 端点位于 `http://<server>:1933/mcp`，与 REST API 同进程、同端口。

## 客户端接入

下表列出已有接入配置。连接后，用当前客户端版本确认能发现工具，并读取一项有权限的数据：

| 平台 | 接入方式 |
|------|----------|
| **Claude Code** | `type: http` 接入 |
| **Trae** | 标准 MCP 配置 |
| **Cursor** | 标准 MCP 配置 |
| **ChatGPT** | 通过自定义 App 接入 OAuth，见 [OAuth 指南](11-oauth.md) |
| **Codex** | 使用 [Codex 集成](../agent-integrations/04-codex.md)中的 MCP 配置 |
| **OpenCode** | OpenCode 原生 `mcp` 配置 |
| **Manus** | 标准 MCP 配置 |
| **Claude.ai / Claude Desktop** | 原生 OAuth 2.1（见 [11-oauth](11-oauth.md)） |

## 鉴权方式

MCP 使用服务端认证配置。API Key 模式下，用 User/Admin key 传入以下任一 header：

- `X-Api-Key: <your-key>`
- `Authorization: Bearer <your-key>`

只有 `dev` 模式无需认证，监听 localhost 本身不会关闭认证。OAuth 客户端使用下文授权流程，其他模式见[认证指南](04-authentication.md)。

## 客户端配置

### 通用 MCP 客户端

支持 `mcpServers` 配置且允许自定义请求头的客户端，可参考以下示例。字段名和传输类型以客户端文档为准：

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

通过 Claude.ai / Claude Desktop 的远程连接器界面接入时，使用下文 OAuth 流程。OpenViking 已经原生实现 OAuth 2.1（DCR + PKCE + opaque token，SQLite 后端，配合 Studio consent 授权页），不再需要外部代理。

按 OAuth 指南在服务端启用 `oauth.enabled` 并配置 HTTPS，再让客户端连接 `https://your-server.com/mcp`，在浏览器中完成授权。

**详见 [OAuth 2.1 接入指南](11-oauth.md)** 和 **[公网访问指南](12-public-access.md)**：

- Studio 授权确认流程，以及可选的 6 字符码跨设备授权
- HTTP（本地）与 HTTPS（生产）两阶段部署，包含 Caddy / nginx 反代模板和 docker-compose 示例
- Claude.ai / Claude Desktop 接入步骤
- `OPENVIKING_PUBLIC_BASE_URL` 与 `oauth` 配置项
- Token 模型（`ovat_` / `ovrt_` / `ovac_` 前缀）与撤销

> 社区项目 [MCP-Key2OAuth](https://github.com/t0saki/MCP-Key2OAuth) Cloudflare Worker 代理是独立的第三方项目。上面的原生流程无需额外部署单元，也不会把 API Key 交给第三方；选择代理时，应单独核对其配置和凭证处理方式。


## 可用的 MCP 工具

以下列出内置 MCP 工具。实际可用工具以所连接服务的 `tools/list` 响应为准：

| 工具 | 说明 | 主要参数 |
|------|------|----------|
| `find` | 无 session 上下文的快速语义检索。只传 `context_type="skill"` 时改走包级 skill 检索：每个 skill 包只返回一条命中，URI 指向该包的 `SKILL.md`，摘要取自 skill 本身，即使命中的是包内辅助文件也是如此；不传 `target_uri` 时同时检索自己的 skill 和账户共享的 `viking://agent/skills`。`skill` 与其它 context_type 混用时仍走通用检索路径 | `query`, `target_uri`(可选), `limit`, `min_score`, `level`(可选), `context_type`(可选), `read_content`(可选——直接内联每条命中的内容) |
| `search` | 深度语义检索；`mode="context"` 组装可直接注入的上下文，并替代原 `recall` 工具。`list` 模式下每个 skill 包也只出一条命中，URI 指向 `SKILL.md`、摘要取自包本身，但 `limit` 在合并之前生效，所以一个包在多个文件上命中时会占掉多个名额，返回条数少于 `limit` | `query`, `mode`（`list` 或 `context`）, `target_uri`（仅 list 模式）, `session_id`(可选), `limit`, `min_score`, `level`（list 模式）, `context_type`(可选)，以及 context 模式的 `quotas`, `purpose`, `max_tokens`, `detail` 或 `detail_by_category`, `dedup_turns`, `exclude_uris`, `peer_scope`, 标量 `other_peer_penalty` 或按类别设置的 `other_peer_penalties`, `rewrite`（`off` 或 `auto`） |
| `read` | 读取一个或多个 `viking://` URI 的内容。PNG、JPEG、GIF、WebP 返回 MCP 原生图片内容；WAV、MP3、FLAC、OGG、M4A 返回原生音频内容。MCP 没有标准视频内容块，因此暂不支持视频 | `uris`（单个字符串或数组）, `offset`, `limit`（文本行数） |
| `list` | 列出 `viking://` 目录下的条目 | `uri`, `recursive`, `offset`, `limit`, `sort_by`, `sort_order`（可选） |
| `tree` | 以缩进形式展示 `viking://` URI 下的递归目录树——当需要全面了解文件树结构时使用（单层列表用 `list`，按文件名查找用 `glob`） | `uri`(可选), `level_limit`(默认 3), `node_limit`(默认 1000), `offset`, `limit`, `include_abstract`(可选——同时展示每个目录的摘要；skill 目录的摘要就是它的名字和描述) |
| `remember` | 存储消息到长期记忆（触发记忆提取） | `messages`（`{role, content}` 列表） |
| `write` | 向 `viking://` 文件写入文本（创建/覆盖/追加）。自动创建缺失的父目录；覆盖前请先用 `read` 查看当前内容；只改文件局部时优先用 `edit`。skill 包不要用它维护：调用方自己的 `skills/` 子树会被拒绝，写 `viking://agent/skills` 则生成绕过安装流程的普通文件，请改用 `add_skill` | `uri`, `content`, `mode`(可选:默认 `replace` — 覆盖或在缺失时创建,`append` — 追加或在缺失时创建,`create` — 已存在则失败), `wait`(可选,阻塞直到重建索引完成), `timeout`(可选) |
| `edit` | 在已有 `viking://` 文件中把精确字符串替换为新文本——用于局部修改，避免整文件重写。若 `old_string` 找不到、或匹配多处且 `replace_all` 为 false，则编辑失败且文件保持不变。编辑 skill 包内的文件不会重新触发 skill 安装流程，请改用 `add_skill` | `uri`, `old_string`, `new_string`, `replace_all`(可选), `wait`(可选,阻塞直到重建索引完成), `timeout`(可选) |
| `add_resource` | 添加本地文件或 URL 作为资源(本地文件触发渐进式上传流) | `path`, `temp_file_id`(可选), `description`(可选), `watch_interval`(可选,分钟数 — 远程 URL 的自动刷新周期), `processing_mode`(可选：默认 `semantic_and_vectors`；传 `vectors_only` 时跳过 VLM 语义理解，只向量化当前文件), `to`(可选,目标 `viking://resources/...` URI；`watch_interval > 0` 时若省略 `to`,watch 将自动绑定到本次 add 创建的资源 URI), `args`(可选,特定 parser 参数，包括 `{"parse_mode":"no_split"}` 用于正常解析但每个源文档只生成一个 Markdown 正文、飞书一次性用户 token 导入使用 `{"feishu_access_token":"u-..."}`，或飞书用户 token watch 使用 access/refresh token，并可选传入 `feishu_app_id` / `feishu_app_secret`) |
| `add_skill` | 新建、安装或替换 agent skill。新 skill 直接传完整 SKILL.md 文本；Git 与 GitHub tree URL 默认安装源里的全部 skill，可用 `skills` 挑选；本地 SKILL.md、目录或 zip 会和 `add_resource` 一样返回签名上传 URL | `data`（SKILL.md 文本）或 `path`（Git URL 或本地路径）, `skills`(可选), `target_uri`(可选；`viking://agent/skills` 表示账户共享), `list_only`(可选) |
| `list_watches` | 列出当前 Agent 可见的 watch 任务（自动刷新订阅），每行显示目标 URI、刷新间隔（分钟）、active/paused 状态以及下一次调度时间 | 无 |
| `cancel_watch` | 按目标 URI 取消（删除）watch 任务。若需调整刷新周期或临时暂停，请取消后使用新的 `watch_interval` 重新添加 | `to_uri`（必须匹配 watch 任务的 `to` 值，例如 `viking://resources/...`） |
| `grep` | 在 `viking://` 文件中进行正则内容搜索 | `uri`, `pattern`（字符串或数组）, `case_insensitive`, `node_limit` |
| `glob` | 按 glob 模式匹配文件 | `pattern`, `uri`(可选范围), `node_limit` |
| `forget` | 删除任意 `viking://` URI（先用 `search` 查找；删除目录需 `recursive=true`）。用它删 skill 目录会残留该 skill 的 privacy 配置，请改用 `ov skills remove` 或 `DELETE /api/v1/skills/{name}` | `uri`, `recursive`（可选） |
| `health` | 检查 OpenViking 服务健康状态 | 无 |

在 MCP 工具中访问自己的工作区，请使用家目录别名 `viking://~`。它在所有控制面
（REST API、`ov` CLI、SDK 和 MCP）上都会展开为 `viking://user/<当前用户>`，因此
`viking://~/notes/todo.md` 会解析成 `viking://user/<当前用户>/notes/todo.md`。
响应始终回显展开后的 canonical URI，这些 canonical URI 也可以直接作为工具入参使用。

无 uid 的写法 `viking://user/<segment>/...`（`memories`、`resources`、`skills`、`peers`、
`privacy`、`sessions`）不再被接受，这类调用会报错并提示改用 `viking://~/...`。
`viking://user` 本身是所有用户空间的容器，而不是自己空间的快捷方式。详见
[Viking URI](../concepts/04-viking-uri.md)。

> **注**：MCP 仅暴露 watch 管理的最小闭包（`list_watches` + `cancel_watch`）。pause / resume / trigger 和统一的 `update` 动作刻意不在此处暴露，请通过 REST `/api/v1/watches/*` 接口或 `ov task watch` CLI 使用上述操作。

> 未传 `args.feishu_access_token` 的飞书/Lark 导入保持现有应用/tenant token 行为，也支持 watch。一次性用户 token 导入只传 `args.feishu_access_token`；用户 token watch 还必须传 `args.feishu_refresh_token`。可为该 watch 同时传入 `args.feishu_app_id` 和 `args.feishu_app_secret`，也可回退使用服务端应用凭证；实际使用的应用必须与用户 token 的签发应用一致。

> `processing_mode=vectors_only` 会跳过 VLM 语义理解阶段，不生成或刷新 `.abstract.md` / `.overview.md`；它只向量化当前非隐藏资源文件，并保留已存在的旧语义产物。

### 添加本地文件资源(单步上传)

`add_resource` 工具同时接受**远程 URL** 和**本地文件路径**。两者的处理路径不同:

- **远程 URL**(`http(s)://`、`git@`、`ssh://`、`git://`):一次调用提交服务端导入。返回 task ID 时，后台处理可能仍在运行，确认[任务状态](../api/17-tasks.md)后再判断内容是否可检索。
- **本地文件路径**:返回**上传指令**(纯文本)。agent 把文件以 `multipart/form-data`(字段名 `file`)POST 到响应里给出的 `temp_upload` URL。该 URL 内嵌一次性 token(默认 10 分钟过期)作为鉴权凭证,无需 API Key。Server 随后在**同一次请求内提交入库**并返回结果,agent **无需**再次调用 `add_resource`。

客户端只要能读取源文件并发出 multipart HTTP 请求，就能上传，无需预装 `ov` CLI。沙箱也需要提供这两项能力，仅传入无法读取的本地路径不能完成文件传输。token 上传复用认证版的 `temp_upload` 路由(API Key 优先,否则走一次性 `?token=`)及其 `TempUploadStore` 持久化,所以 `local` / `shared` 上传模式行为一致。注意:一次性 token 保存在进程内,因此多 worker 部署下 `add_resource` 调用与后续的上传 POST 必须落到同一个 worker(或以单 worker 运行),token 才能被解析。

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
    # 推荐优先使用 ghcr.io；如果访问有问题，可改用 openviking-cn-beijing.cr.volces.com/volcengine/openviking:latest
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

**可能原因：** 客户端的 API Key 无效、已过期，或不适用于租户数据访问。

**解决方案：** 确认客户端使用目标 account 的有效 user/admin key；`api_key` 模式下，root key 只用于管理接口。参见[认证指南](04-authentication.md)。

## 参考

- [MCP 规范](https://modelcontextprotocol.io/)
- [OpenViking 配置](01-configuration.md)
- [OpenViking 部署](03-deployment.md)

客户端配置参考：[Claude Code MCP](https://code.claude.com/docs/en/mcp)、[OpenCode MCP servers](https://opencode.ai/docs/mcp-servers/)。
