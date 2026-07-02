# OpenViking 0.3.x 到 0.4.0 升级指南

本文面向已经运行 OpenViking 0.3.x 的用户，说明升级到 0.4.0 前后需要做什么、哪些旧用法仍然兼容、数据迁移如何执行，以及业务代码如何逐步迁移到新模型。

## 是否需要升级

如果继续停留在 0.3.x，现有 `agent_id`、`viking://agent/...`、`viking://session/...` 行为不会变化，但也无法使用 0.4.0 的能力：

- 没有 User / Peer 数据模型。
- 没有 legacy agent/session 数据迁移和 cleanup 命令。
- 没有 `actor_peer_id` 请求级 peer 视图。
- 后续围绕新模型的修复和能力不会回补到旧模型。

如果升级到 0.4.0，可以先不迁移数据。0.4.0 提供运行期兼容，旧数据不会因为升级立即不可读：

- `agent_id` 仍可临时配置。当前 HTTP SDK client 会把它映射成请求级 `actor_peer_id`；只有仍发送 `X-OpenViking-Agent` 的请求才进入真正的 legacy agent 模式。
- `viking://agent/...` 仍可读旧 agent 数据，但只读。
- `viking://session/...` 仍可读旧 session 数据，并会合并新 session 视图。
- 只有真正发送 `X-OpenViking-Agent` 的 legacy 请求会让 `find` / `search` 同时查未迁移的旧 agent 数据；HTTP SDK `agent_id` 现在只选择 actor peer 视图。

推荐顺序：

```text
备份
  -> 升级 server / CLI / SDK
  -> 验证旧数据仍可读
  -> 执行数据迁移
  -> 验证新路径
  -> 逐步迁移业务用法
  -> 可选 cleanup
```

## 升级前备份

先用 0.3.x 兼容版本创建备份。建议使用 `0.3.24`：

```bash
pip install openviking==0.3.24 --upgrade --force-reinstall
ov backup ./backups/openviking-before-0.4.0.ovpack
```

确认当前版本：

```bash
python -c "import openviking; print(openviking.__version__)"
ov version
```

不要在 0.3.x 上执行 `ov --sudo admin migrate`。迁移命令只在 0.4.0 或更新版本可用。

## 升级服务和客户端

安装 0.4.0，重启服务端，并确保 CLI / SDK 也升级到同一版本线：

```bash
pip install openviking==0.4.0 --upgrade --force-reinstall
openviking-server --config ov.conf
```

如果使用仓库内 Rust `ov` CLI，需要重新构建或安装 CLI；否则本地 `ov` 可能仍是旧二进制。

升级后先验证配置和旧数据读取：

```bash
ov config validate
ov ls viking://agent
ov ls viking://session
ov session list
```

`viking://session` 的兼容合并发生在服务端。只升级 CLI、不重启 server，不会改变服务端读取行为。

## 兼容性速查

| 旧用法 | 0.4.0 行为 |
| --- | --- |
| client 配置 `agent_id` | 支持。当前 HTTP SDK client 会映射成请求级 `actor_peer_id`；它本身不再触发 legacy agent 模式。 |
| `ov ls viking://agent` | 支持读；如果设置了 `agent_id` / `actor_peer_id`，只显示当前 actor peer 对应的 legacy agent。 |
| 读 `viking://agent/<agent_id>/...` | 支持读旧数据。 |
| 写 `viking://agent/...` | 不支持。新写入应进入 `viking://user/<user_id>/peers/<peer_id>/...`。 |
| `ov ls viking://session` | 支持读，会合并新 session 和旧 session。 |
| 读 `viking://session/<session_id>/...` | 支持读，按新路径优先、旧路径兜底。 |
| 写 `viking://session/...` | 不支持。新 session 写入 `viking://user/<user_id>/sessions/...`。 |
| HTTP SDK `find` / `search` 传 `agent_id` | 支持，只查选中的 actor peer 视图；只有发送 `X-OpenViking-Agent` 的真正 legacy 请求才会查旧 agent 数据。 |
| `find` / `search` body 传旧 `peer_id` | 不支持。新 peer 视图使用 `actor_peer_id` 或 `X-OpenViking-Actor-Peer`。 |
| 同时配置 `actor_peer_id` 和 `agent_id` | 不支持，会报错。 |
| HTTP SDK `agent_id` client 下显式传 message `peer_id` | 支持。该 message 使用显式 `peer_id`；只有没有 message `peer_id` 时才走 legacy fallback。 |
| `role_id` 记忆隔离 | 不再支持，升级后忽略。 |

## 执行数据迁移

确认升级后旧数据可读，再执行迁移：

```bash
ov --sudo admin migrate --output json
```

响应会返回 task id：

```json
{
  "task_id": "..."
}
```

查询任务：

```bash
ov --sudo task status <task_id>
ov --sudo task list --task-type legacy_migration
```

HTTP API：

```http
POST /api/v1/admin/migrate
X-API-Key: <root-key>
```

请求体可以为空，等价于：

```json
{
  "action": "migrate"
}
```

查询任务：

```http
GET /api/v1/tasks/{task_id}
X-API-Key: <root-key>
```

ROOT 查询迁移任务时不会按普通 account/user 过滤。迁移会为整个存储创建一个 root 级别 task，不会按 account 分别创建 task。

## 迁移规则

0.4.0 的新模型是 User / Peer：

```text
User = 自然人或业务使用者
Peer = User 下的交互对象
Session = User 下的会话状态
Skill = User 下的可执行技能
```

迁移目标：

| 旧数据 | 新位置 |
| --- | --- |
| `viking://agent/<agent_id>/memories/...` | `viking://user/<user_id>/peers/<agent_id>/memories/...` |
| `viking://agent/<agent_id>/resources/...` | `viking://user/<user_id>/peers/<agent_id>/resources/...` |
| `viking://agent/<agent_id>/skills/<skill>/...` | `viking://user/<user_id>/skills/<skill>/...` |
| `viking://session/<session_id>/...` | `viking://user/<user_id>/sessions/<session_id>/...` |

共享 legacy agent 数据会复制到每个目标 user 的 peer 目录。如果旧路径已经表达了 user owner，只迁移到该 user。

迁移会一并处理已有向量索引：对实际复制成功的 memory / resource / skill 文件或目录，直接读取旧记录中的 `vector` / `sparse_vector` 和标量字段，重写 URI 后写入新记录。迁移不会重新向量化，也不会自动调用 `reindex`。共享 legacy agent 数据复制到多个 user 时，会按每个目标 user URI 写入多份向量记录。

没有向量 payload 的旧标量记录会跳过并计入 `migrated.skipped_vector_records`。Session 迁移只复制文件状态，不处理向量索引。

Session owner 按以下顺序解析：

1. `.meta.json.created_by_user_id`
2. `.meta.json.user_id`、`.meta.json.owner_user_id` 或 `.meta.json.created_by`
3. 旧路径里的 user hint，例如 `/session/alice/sess-001`
4. 单用户 account 下的唯一注册用户

多用户 account 下，如果某个 legacy session 无法识别 owner，preflight 会失败。升级后的运行期兼容可以临时读取旧 session，但正式迁移前仍应补齐 owner。

Legacy agent instructions 不迁移：

```text
viking://agent/<agent_id>/instructions
```

迁移会记录 warning，不创建替代目录。

## 迁移前检查

以下问题会在 task 创建前直接失败：

- 物理存储中存在 legacy 数据，但对应 account 不在 API key user registry 中。
- 多用户 account 下存在无法识别 owner 的 legacy session。
- session owner 存在，但不是合法的 OpenViking user id。

以下问题会记录为 warning 或 skipped，并继续迁移：

- 目标 user 已经存在同名 skill。旧 skill 会被跳过，不覆盖现有 skill。
- 发现 legacy agent instructions。Instructions 不迁移。
- 存在共享 legacy agent，但 account 下没有可迁移的目标 user。

如果迁移发现 legacy 数据 owner 不在 user registry 中，会自动注册该 user。迁移结果只记录自动创建了哪些用户，不返回明文 user key。

如果开启了 `api_key_hashing`，明文 key 无法从存储中反查。需要重新生成：

```bash
ov --sudo admin regenerate-key <account_id> <user_id>
```

## 验证迁移结果

查看任务结果：

```bash
ov --sudo task status <task_id>
```

重点看：

- `migrated.files` / `migrated.directories`
- `migrated.vector_records` / `migrated.skipped_vector_records`
- `migrated.operations`
- `skipped`
- `warnings`
- `created_users`

验证新路径：

```bash
ov ls viking://user/<user_id>/peers/<agent_id>/memories
ov ls viking://user/<user_id>/skills
ov ls viking://user/<user_id>/sessions
```

迁移只复制数据，不删除 legacy 路径或旧向量记录。重复执行是幂等的：已存在的目标文件和 skill 会被跳过，不会覆盖。如果迁移后的检索结果不符合预期，再由用户对新路径手动执行 `reindex`；迁移流程本身不会触发 reindex。

## 业务用法迁移

### Client 配置

旧配置可以先继续用：

```json
{
  "agent_id": "legacy-agent"
}
```

推荐逐步改成：

```json
{
  "actor_peer_id": "legacy-agent"
}
```

不要同时配置：

```json
{
  "actor_peer_id": "customer-a",
  "agent_id": "legacy-agent"
}
```

这会报错。

### 文件路径

旧路径：

```text
viking://agent/code-agent/memories/profile.md
viking://session/sess-001/messages.jsonl
```

新路径：

```text
viking://user/alice/peers/code-agent/memories/profile.md
viking://user/alice/sessions/sess-001/messages.jsonl
```

`viking://session/<session_id>` 可以继续作为当前 user session 的读 alias 使用，但新写入和长期引用建议使用 `viking://user/<user_id>/sessions/<session_id>`。

### find / search

迁移窗口内，如果还需要读取未迁移的旧 agent 数据，需要使用仍发送 `X-OpenViking-Agent` 的真正 legacy 请求路径。当前 HTTP SDK `agent_id` 是 actor-peer alias，不再让 `find` / `search` 包含旧 agent 数据：

```json
{
  "query": "deployment notes",
  "agent_id": "legacy-agent"
}
```

迁移完成并确认不再需要旧 agent 数据后，所有 client 都改为 client/request 级 `actor_peer_id`。

### 会话消息

没有显式 assistant message `peer_id` 的 legacy 请求仍可 fallback 到 legacy agent id。当前 HTTP SDK client 不应再依赖 `agent_id` 做消息归属；需要表达说话人时使用 message `peer_id`。

## 暂不迁移数据

升级后可以暂时不迁移，但要知道这些限制：

- 旧 agent/session 数据可读，但旧 namespace 不可写。
- 新 session 和新资源会写入新 namespace，数据会在新旧路径并存一段时间。
- 发送 `X-OpenViking-Agent` 的真正 legacy 检索会同时查新旧数据；HTTP SDK `agent_id` 和纯 `actor_peer_id` 不会默认查旧 `viking://agent`。
- 多用户 account 下 owner 不明确的旧 session，运行期可能可读，但正式迁移会被 preflight 拦截。
- cleanup 之前旧目录和旧向量记录仍会保留。

因此不迁移适合作为短期过渡，不建议作为长期状态。

## 可选 cleanup

确认迁移结果无误后，可以删除旧 namespace：

```bash
ov --sudo admin migrate --cleanup --output json
ov --sudo task status <cleanup_task_id>
```

HTTP 请求体：

```json
{
  "action": "cleanup"
}
```

Cleanup 只删除：

```text
/local/<account>/agent
/local/<account>/session
/local/<account>/user/<user>/agent
```

Cleanup 会先删除上述 legacy URI scope 下的旧向量记录，再删除对应 AGFS 目录。若向量读取或删除失败，该目录会被跳过，避免旧文件已删除但旧索引仍残留。Cleanup 不会删除新 user / peer 路径下的文件或向量记录。

不会删除新模型目录：

```text
/local/<account>/user/<user>/peers
/local/<account>/user/<user>/sessions
/local/<account>/user/<user>/skills
```

Cleanup 后，`viking://agent/...` 不再用于读取迁移后的 peer 数据；请使用新路径。`viking://session/...` 仍可作为当前 user session 的 alias 读取新 session。

## 常见问题

### ov ls viking://agent 只看到一个 agent

如果配置了 `agent_id` 或 `actor_peer_id`，这是预期行为。`viking://agent` 根目录会过滤到当前 actor peer，只显示对应 legacy agent。

### ov ls viking://session 仍为空

确认服务端已经重启并加载 0.4.0。`viking://session` 的合并读发生在服务端；只升级 CLI 不会改变服务端读取行为。

### 配置同时有 actor_peer_id 和 agent_id

这是不允许的。保留 `agent_id` 进入 legacy 模式，或删除 `agent_id` 后改用 `actor_peer_id`。

### Preflight 报告 unknown account

物理存储中存在某个 account 的 legacy 数据，但 API key registry 中没有这个 account。先恢复或重新创建该 account，再重新执行迁移。

### Preflight 报告 unresolved session owner

给 legacy session 的 `.meta.json` 补充 owner 字段，或把 session 移到能明确识别 owner 的旧路径下，然后重新执行迁移。

### 某个 skill 没有迁移

查看 task 的 `skipped` 列表。最常见原因是目标 user 已经存在同名 skill。迁移不会覆盖现有 skill。
