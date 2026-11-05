# Peer

Peer 是当前用户空间中的稳定交互对象，可以代表一个人或另一个 Agent。为对象使用固定的 `peer_id`，可以把相关资料和记忆组织在一起，并在后续会话中继续使用。

例如，Alice 的助手经常与 `review-agent` 协作评审。Alice 自己的偏好保存在用户记忆中，与 review-agent 有关的评审偏好则可以保存在对应的 Peer 记忆中。

## 区分身份与对象

| 概念 | 本例中的含义 |
| --- | --- |
| Account：`acme` | Alice 所属团队 |
| User：`alice` | 数据所有者和访问服务的身份 |
| Peer：`review-agent` | Alice 的稳定交互对象 |
| Session | Alice 与对象的一次对话 |

`review-agent` 的内容位于 `viking://user/alice/peers/review-agent/`。Peer ID 不会创建一个独立 User，也不会为该对象签发访问凭据。即使把 Peer 命名为 `bob`，它也仍然是 Alice 空间中的对象，与真实用户 Bob 的个人空间分别管理。

## 准备工作

先完成[认证](17-authentication.md)，使用 `acme-alice` 连接。以下例子先验证 Peer 资料范围，再演示记忆提取；写入资料和提取记忆需要可用的服务端模型，会产生模型调用。

为同一个对象保持稳定 ID，例如 `review-agent`；使用单段标识，避免在 ID 中使用 `/`。重复体验时更换会话 ID，保留同一个 Peer ID 即可延续该对象的上下文。

## 保存对象资料

Alice 为两个协作对象分别建立资料目录：

```bash
ov config switch acme-alice
ov mkdir viking://~/peers/review-agent/resources
ov mkdir viking://~/peers/research-agent/resources
```

为 review-agent 写入一份协作说明：

```bash
ov write viking://~/peers/review-agent/resources/brief.md \
  --mode create --content "review-agent 负责评审，评审建议按问题、影响、建议三列组织。" --wait
```

处理完成后，读取原文，确认内容：

```bash
ov read viking://~/peers/review-agent/resources/brief.md
```

如果写入等待超时，保存返回的任务 ID，用 `ov task status <task_id>` 查询状态；完成后再继续。

## 限定 Peer 范围

在 Alice 的同一连接上，使用 `actor_peer_id` 将一次请求的 Peer 集合限制到 review-agent：

```bash
ov --actor-peer-id review-agent ls viking://~/peers
ov --actor-peer-id review-agent read viking://~/peers/review-agent/resources/brief.md
```

列表只应包含允许的 Peer，读取说明文件应成功。再尝试访问另一个 Peer：

```bash
ov --actor-peer-id review-agent ls viking://~/peers/research-agent/resources
```

这次访问应被拒绝。需要按问题查找 review-agent 的资料时，可以限定具体目录：

```bash
ov --actor-peer-id review-agent find "评审建议使用什么格式？" \
  --uri viking://~/peers/review-agent/resources
```

Actor Peer 只限制当前用户下的 Peer 集合。用户自身的记忆、资料和可访问的团队共享资料仍按原权限可见，会话也仍属于 Alice。应用面向外部参与者时，应由可信入口决定 Peer 范围；持有 Alice 完整 Key 的客户端可以改变或省略这个范围。独立用户权限使用不同 User 身份。

## 保存 Peer 记忆

消息中的 `peer_id` 用来标识交互对象，`actor_peer_id` 用于请求的访问范围，两者分别设置。本例只提取该对象的偏好，通过 HTTP 创建会话并显式配置记忆策略。

在 Alice 的终端设置服务地址，通过隐藏输入提供她自己的 User Key：

```bash
export OV_URL="http://127.0.0.1:1933"
read -r -s ALICE_KEY
export ALICE_KEY
```

创建会话，开启 Peer 记忆、关闭本次会话的 Self 记忆提取，并使用手动提交：

```bash
curl -sS -X POST "$OV_URL/api/v1/sessions" \
  -H "X-API-Key: $ALICE_KEY" \
  -H "Content-Type: application/json" \
  -d '{"session_id":"peer-review-a","auto_commit_policy":null,"memory_policy":{"self":{"enabled":false},"peer":{"enabled":true},"memory_types":["preferences"]}}'
```

检查响应成功后，仍使用 Alice 的 CLI 连接记录消息并提交：

```bash
ov session add-message peer-review-a --role user --peer-id review-agent \
  --content "我是 review-agent。我偏好将评审建议整理为问题、影响、建议三列的表格；后续与我协作评审时请沿用这个格式。"
ov session commit peer-review-a
```

记录返回的 `task_id` 和 `archive_uri`。每隔 5 秒查询一次任务，先观察最多 2 分钟；仍在运行时保留任务 ID，稍后继续。状态为 `failed` 或 `cancelled` 时查看原因：

```bash
ov task status <task_id>
```

状态为 `completed` 后读取变更记录：

```bash
ov read "<archive_uri>/memory_diff.json"
```

在 `operations.adds` 或 `operations.updates` 中查找对应记忆 URI，再用 `ov read` 读取。预期记录位于 `viking://user/alice/peers/review-agent/memories/` 下，正文保留三列表格的偏好。文件名由提取结果决定；若没有新增记录，检查是否更新了已有记忆，以及当前模板是否支持 Peer 和 preferences 类型。

## 在后续任务中使用

在新的对话中，让使用 Alice 连接的 Agent 先检索该 Peer 的记忆：

```bash
ov --actor-peer-id review-agent find "review-agent 的评审格式偏好" \
  --uri viking://~/peers/review-agent/memories
```

读取命中的记忆，再让 Agent 按该偏好生成评审表格并引用记忆 URI。新会话继续使用 `review-agent` 标记相关消息，后续提交可持续积累该对象的记忆。

## 下一步

Peer 内容属于当前用户。需要让团队其他用户使用选定知识时，继续[知识共享](22-sharing.md)；完整过滤规则见[多租户](../concepts/11-multi-tenant.md#peer-restricted-view)。
