# 在导入资源时设置检索标签

OpenViking 支持在 `add_resource` 时直接设置显式检索标签。标签会写入资源的向量检索元数据，并可在 `find` / `search` 时作为过滤条件使用。

适用场景：

- 给资源标记来源：`source=github`、`source=upload`
- 区分环境：`env=prod`、`env=test`
- 标记业务线或团队：`team=search`
- 标记数据批次：`batch=20260727`
- 给稳定来源的 watch 资源绑定持续生效的标签策略

## 1. 基本概念

### 1.1 标签格式

标签必须是严格的 `k=v` 格式：

```text
team=search
env=prod
source=upload
```

服务端会自动规范化：

- 去掉首尾空白
- 转小写
- 去重

例如：

```json
[" Team=Search ", "team=search", "Env=Prod"]
```

会变成：

```json
["team=search", "env=prod"]
```

非法标签会被拒绝：

```text
team
team=
=search
a=b=c
```

### 1.2 replace 和 append

`tag_mode` 控制写入模式：

| 模式 | 说明 |
| --- | --- |
| `replace` | 替换已有标签。默认模式。 |
| `append` | 合并到已有标签。同 key 后写覆盖先写。 |

示例：

已有标签：

```json
["env=dev", "team=search"]
```

追加：

```json
["env=prod", "owner=qa"]
```

结果：

```json
["env=prod", "team=search", "owner=qa"]
```

## 2. HTTP 用法

### 2.1 导入本地或远程资源时设置标签

```bash
curl -sS -X POST "$OV_URL/api/v1/resources" \
  -H "X-API-Key: $OV_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "path": "/tmp/demo.md",
    "to": "viking://resources/demo.md",
    "wait": true,
    "timeout": 120,
    "tags": ["team=search", "env=test"],
    "tag_mode": "replace"
  }'
```

成功后，`tags` 会随本次导入生成的向量检索记录一起写入。`add_resource`
响应本身不再额外返回 `tags_result`：

```json
{
  "status": "ok",
  "result": {
    "root_uri": "viking://resources/demo.md",
    "queue_status": {
      "status": "completed"
    }
  }
}
```

可以随后用 search/find 的 `tags` 参数验证召回是否只命中这批资源。

### 2.2 上传文件并设置标签

先上传文件：

```bash
TEMP_ID="$(
  curl -sS -X POST "$OV_URL/api/v1/resources/temp_upload" \
    -H "X-API-Key: $OV_API_KEY" \
    -F "file=@/tmp/demo.md;type=text/markdown" |
  jq -r '.result.temp_file_id'
)"
```

再导入并设置标签：

```bash
curl -sS -X POST "$OV_URL/api/v1/resources" \
  -H "X-API-Key: $OV_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"temp_file_id\": \"$TEMP_ID\",
    \"to\": \"viking://resources/uploads/demo.md\",
    \"wait\": true,
    \"timeout\": 120,
    \"tags\": [\"source=upload\", \"env=test\"],
    \"tag_mode\": \"replace\"
  }"
```

### 2.3 导入后追加标签

```bash
curl -sS -X POST "$OV_URL/api/v1/fs/attrs/set_tags" \
  -H "X-API-Key: $OV_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "uri": "viking://resources/uploads/demo.md",
    "tags": ["owner=qa"],
    "mode": "append",
    "recursive": true
  }'
```

### 2.4 导入后替换标签

```bash
curl -sS -X POST "$OV_URL/api/v1/fs/attrs/set_tags" \
  -H "X-API-Key: $OV_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "uri": "viking://resources/uploads/demo.md",
    "tags": ["owner=release", "source=curated"],
    "mode": "replace",
    "recursive": true
  }'
```

`replace` 会移除旧标签，只保留本次输入的标签。

## 3. 使用标签检索

### 3.1 search

```bash
curl -sS -X POST "$OV_URL/api/v1/search/search" \
  -H "X-API-Key: $OV_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "deployment guide",
    "target_uri": "viking://resources",
    "limit": 5,
    "tags": ["team=search", "env=prod"]
  }'
```

### 3.2 find

```bash
curl -sS -X POST "$OV_URL/api/v1/search/find" \
  -H "X-API-Key: $OV_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "deployment guide",
    "target_uri": "viking://resources",
    "limit": 5,
    "tags": ["team=search"]
  }'
```

### 3.3 多标签是 AND 语义

下面的请求只会召回同时带有 `team=search` 和 `env=prod` 的结果：

```json
{
  "tags": ["team=search", "env=prod"]
}
```

如果结果只有 `team=search`，没有 `env=prod`，不会被召回。

## 4. CLI 用法

### 4.1 导入资源并替换标签

```bash
ov add-resource ./demo.md \
  --to viking://resources/demo.md \
  --wait \
  --tag team=search \
  --tag env=test \
  --tag-mode replace
```

### 4.2 导入资源并追加标签

```bash
ov add-resource ./demo.md \
  --to viking://resources/demo.md \
  --wait \
  --tag owner=qa \
  --tag-mode append
```

说明：

- `--tag` 可以重复。
- `--tag-mode` 可选值为 `replace` 和 `append`。
- 不传 `--tag-mode` 时默认是 `replace`。

## 5. Python SDK 用法

### 5.1 AsyncOpenViking

```python
from openviking_sdk import AsyncOpenViking

client = AsyncOpenViking(api_key="...", base_url="http://127.0.0.1:1933")

result = await client.add_resource(
    path="/tmp/demo.md",
    to="viking://resources/demo.md",
    wait=True,
    tags=["team=search", "env=test"],
    tag_mode="replace",
)

print(result["root_uri"])
```

### 5.2 OpenViking

```python
from openviking_sdk import OpenViking

client = OpenViking(api_key="...", base_url="http://127.0.0.1:1933")

result = client.add_resource(
    path="/tmp/demo.md",
    to="viking://resources/demo.md",
    wait=True,
    tags=["team=search"],
    tag_mode="append",
)
```

### 5.3 更新已有资源标签

```python
await client.set_tags(
    "viking://resources/demo.md",
    ["owner=qa"],
    mode="append",
    recursive=True,
)
```

### 5.4 带标签检索

```python
result = await client.search(
    query="deployment guide",
    target_uri="viking://resources",
    tags=["team=search", "env=prod"],
    limit=5,
)
```

```python
result = await client.find(
    query="deployment guide",
    target_uri="viking://resources",
    tags=["team=search"],
    limit=5,
)
```

## 6. Watch 场景

### 6.1 稳定来源 watch 的标签策略

URL、sitemap、RSS 等可重复读取的稳定来源可以创建 watch，并保存标签策略：

```bash
curl -sS -X POST "$OV_URL/api/v1/resources" \
  -H "X-API-Key: $OV_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "path": "https://example.com/docs/sitemap.xml",
    "to": "viking://resources/watch-demo",
    "wait": true,
    "timeout": 120,
    "watch_interval": 300,
    "tags": ["source=sitemap", "watch=true"],
    "tag_mode": "replace"
  }'
```

watch task 会保存标签策略：

```json
{
  "processor_kwargs": {
    "tags": ["source=sitemap", "watch=true"],
    "tag_mode": "replace"
  }
}
```

后续 watch refresh 会沿用这组标签。

### 6.2 上传文件不支持 watch

通过 `temp_upload` 上传的文件是一次性快照，不能和 `watch_interval > 0` 组合使用。即使请求里带了 `tags`，服务端也会返回 `INVALID_ARGUMENT`，不会创建 watch task，也不会创建 `watch_sources` 目录。

不支持的请求示例：

```bash
curl -sS -X POST "$OV_URL/api/v1/resources" \
  -H "X-API-Key: $OV_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"temp_file_id\": \"$TEMP_ID\",
    \"to\": \"viking://resources/watch-demo.md\",
    \"wait\": true,
    \"watch_interval\": 300,
    \"tags\": [\"source=upload\", \"watch=true\"],
    \"tag_mode\": \"replace\"
  }"
```

如果上传文件变化，需要重新上传，并再次调用 `add_resource(tags=...)`。

### 6.3 停用 watch

如果要停用 watch，可以对同一个 `to` 设置 `watch_interval=0`：

```bash
curl -sS -X POST "$OV_URL/api/v1/resources" \
  -H "X-API-Key: $OV_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"path\": \"https://example.com/docs/sitemap.xml\",
    \"to\": \"viking://resources/watch-demo.md\",
    \"wait\": true,
    \"timeout\": 120,
    \"watch_interval\": 0,
    \"tags\": [\"source=upload\", \"watch=cancelled\"],
    \"tag_mode\": \"replace\"
  }"
```

如果刚触发过 watch，同一个 URI 可能短时间处于 busy 状态。遇到 retryable `409 CONFLICT` 时，稍后重试即可。

## 7. 查看标签

可以通过 `fs attrs` 查看当前 URI 聚合到的检索标签：

```bash
curl -sS -G "$OV_URL/api/v1/fs/attrs" \
  -H "X-API-Key: $OV_API_KEY" \
  --data-urlencode "uri=viking://resources/demo.md"
```

返回示例：

```json
{
  "status": "ok",
  "result": {
    "uri": "viking://resources/demo.md",
    "context_type": "resource",
    "attrs": {
      "tags": ["team=search", "env=test"]
    }
  }
}
```

## 8. 常见问题

### 8.1 `add_resource(tags=...)` 没有返回 `tags_result` 是正常的吗？

正常。`add_resource` 的 tags 会在导入过程中随每条向量检索记录一起写入，
不会在完成后再调用 `set_tags`，因此响应里没有批量更新结果。可以用
`/api/v1/search/search` 或 `/api/v1/search/find` 带 tags 验证召回。

### 8.2 `set_tags.tags_updated=false` 是什么意思？

这表示本次显式 `set_tags` 没有找到可更新的向量检索记录，或者目标记录尚未对当前后端可见。常见原因：

- 资源导入没有生成向量记录。
- 后端存在短暂最终一致性延迟。
- URI 指向的不是带语义记录的资源节点。

可以稍后调用 `/api/v1/fs/attrs/set_tags` 显式重试。

### 8.3 `set_tags` 返回成功，但 `fs attrs` 还是空怎么办？

先区分两种情况：

- local backend：通常应该立即可见。如果不可见，优先检查 URI、account/user、`recursive`。
- remote vector backend：可能存在写入可见性延迟，或者 backend 的 update/upsert 语义没有真正持久化 `search_tags`。

排查顺序：

1. 如果是 `add_resource(tags=...)`，用 `/api/v1/search/search` 或 `/api/v1/search/find` 带 tags 验证召回。
2. 查 `GET /api/v1/fs/attrs?uri=...`。
3. 如果是显式 `set_tags`，查看它的 `tags_result`。
4. 如有 debug 权限，查 vector record 的 `search_tags` 字段。

### 8.4 为什么 tag 必须是 `k=v`？

`k=v` 让标签可组合、可覆盖、可审计。`append` 模式可以按 key 覆盖旧值，例如 `env=dev` 追加 `env=prod` 后变为 `env=prod`，避免同一维度出现多个冲突值。

### 8.4 多个 tags 是 OR 还是 AND？

AND。只有同时包含所有请求标签的结果才会被召回。

### 8.5 tags 会影响原始文件内容吗？

不会。tags 只写入向量检索元数据 `search_tags`，不会修改原始文件内容。

### 8.6 tags 会跨账号可见吗？

不会。检索和标签读写都受 account/user 上下文隔离。另一个 account 即使使用相同 URI 和相同 tag，也不应该召回当前 account 的资源。

## 9. 最小验证脚本

下面是一组最小 curl 验证流程：

```bash
export OV_URL="http://127.0.0.1:1933"
export OV_API_KEY="test"

TEMP_ID="$(
  curl -sS -X POST "$OV_URL/api/v1/resources/temp_upload" \
    -H "X-API-Key: $OV_API_KEY" \
    -F "file=@/tmp/demo.md;type=text/markdown" |
  jq -r '.result.temp_file_id'
)"

curl -sS -X POST "$OV_URL/api/v1/resources" \
  -H "X-API-Key: $OV_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"temp_file_id\": \"$TEMP_ID\",
    \"to\": \"viking://resources/tag-demo.md\",
    \"wait\": true,
    \"timeout\": 120,
    \"tags\": [\"source=curl\", \"env=test\"],
    \"tag_mode\": \"replace\"
  }" | jq .

curl -sS -X POST "$OV_URL/api/v1/search/search" \
  -H "X-API-Key: $OV_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "demo",
    "target_uri": "viking://resources",
    "limit": 5,
    "tags": ["source=curl", "env=test"]
  }' | jq .
```

如果第二个请求能召回资源，说明 tags 已经参与检索过滤。
