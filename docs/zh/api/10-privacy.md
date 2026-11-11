# 隐私配置

隐私配置在当前用户空间内，按 `category + target_key` 管理技能、会话中的敏感信息（如密钥、凭据等）。配置值变化时会生成版本快照，可查询历史版本并切换生效版本。提交与当前版本相同的值不会创建新版本。

以下示例使用当前用户的 API Key。trusted 模式需补充部署要求的身份头。读取值的接口和 CLI 命令会返回实际值；树总览和字段元数据接口不返回值。下文的 `***` 仅为示例占位符，不表示接口自动脱敏。

激活版本只改变 OpenViking 保存的配置，不会替外部服务轮换、撤销或恢复密钥。

## 典型场景

- 为某个 skill 或 session 保存密钥等敏感配置
- 轮换密钥（新版本）
- 回滚到历史版本
- 在读取 skill 或 session 内容时按占位符恢复配置值

---

## 接口总览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/privacy-configs` | 列出隐私配置分类 |
| GET | `/api/v1/privacy-configs/tree` | 树形列出当前目标和字段元数据，不返回值 |
| POST | `/api/v1/privacy-configs/scan` | 扫描或修复已存储正文中的登记值 |
| GET | `/api/v1/privacy-configs/{category}` | 列出分类下目标 |
| GET | `/api/v1/privacy-configs/{category}/{target_key}` | 获取当前生效配置（meta + current） |
| GET | `/api/v1/privacy-configs/{category}/{target_key}/fields` | 列出不含明文值的安全字段元数据 |
| GET | `/api/v1/privacy-configs/{category}/{target_key}/field?field={field}` | 获取单字段引用；显式选择时返回值 |
| POST | `/api/v1/privacy-configs/resolve` | 按字段 reference/URI 显式读取值 |
| POST | `/api/v1/privacy-configs/{category}/{target_key}` | 写入新版本并激活 |
| PATCH | `/api/v1/privacy-configs/{category}/{target_key}` | 原子合并字段；目标不存在时创建 |
| DELETE | `/api/v1/privacy-configs/{category}/{target_key}` | 删除目标及全部版本 |
| DELETE | `/api/v1/privacy-configs/{category}/{target_key}/field?field={field}` | 删除当前生效配置中的字段 |
| DELETE | `/api/v1/privacy-configs/selector` | 按 target URI 或字段 reference/URI 删除 |
| GET | `/api/v1/privacy-configs/{category}/{target_key}/versions` | 列出版本号 |
| GET | `/api/v1/privacy-configs/{category}/{target_key}/versions/{version}` | 获取指定版本详情 |
| POST | `/api/v1/privacy-configs/{category}/{target_key}/activate` | 激活指定版本 |

---

## 数据结构

### current（当前生效版本）

```json
{
  "version": 3,
  "category": "skill",
  "target_key": "byted-viking-search-knowledgebase",
  "values": {
    "api_key": "***",
    "base_url": "https://example.com"
  },
  "created_at": "2026-04-27T10:00:00+08:00",
  "created_by": "alice",
  "change_reason": "rotate key"
}
```

### meta（元信息）

```json
{
  "category": "skill",
  "target_key": "byted-viking-search-knowledgebase",
  "active_version": 3,
  "latest_version": 5,
  "created_at": "2026-04-21T10:00:00+08:00",
  "updated_at": "2026-04-27T10:00:00+08:00",
  "updated_by": "alice",
  "last_accessed_at": "2026-04-27T10:00:00+08:00",
  "labels": {
    "env": "prod"
  }
}
```

### 输入格式与容量限制

服务端对 HTTP、CLI、Embedded 和内部写入执行同一套验证：

| 项目 | 限制 |
|------|------|
| `category` / `target_key` | 非空、最多 255 个 ASCII 字符；仅允许字母、数字、`_`、`-`、`.`、`@`；禁止 `.`、`..`，`@` 最多一个 |
| Session 的 `target_key` | 当 Session ID 符合上行的限制时，使用 Session ID 作为 `target_key`；否则为 `_` 加 SHA-256 前 24 位十六进制（原始 Session ID 会自动记录在 `labels.session_id`） |
| values 顶层字段 | `category=session` 最多 1024 个；其他类别最多 128 个 |
| 字段名 | 非空、最多 255 UTF-8 字节、禁止控制字符 |
| 单个字符串值 | 最多 64 KiB UTF-8 |
| values 序列化总量 | 最多 1 MiB |
| JSON 嵌套 | 最多 16 层；单个对象或数组最多 4096 项 |
| labels | 最多 64 项，序列化总量最多 64 KiB |
| `change_reason` | 最多 1024 个字符 |

违反限制时返回 `INVALID_ARGUMENT`，`details` 包含字段、原因、实际大小和允许上限。错误信息不会包含敏感值正文。

---

## API 参考

### scan_privacy_content()

显式扫描一个已经持久化的文件或目录。默认 dry-run，不返回命中的原值；`fix=true` 时替换无歧义命中，并异步刷新相应内容。

```text
POST /api/v1/privacy-configs/scan
```

```json
{
  "uri": "viking://~/resources/project-a",
  "fix": false,
  "node_limit": 1000
}
```

`uri` 必填，服务端刻意不支持按 scope 发起全域扫描。`node_limit` 只能在 1–1000 之间。服务端还强制限制为每个进程同时一个扫描、每秒最多读取 20 个文件、单文件最多 1 MiB、总输入最多 16 MiB、单文件最多记录 1000 个替换、每个命中文件最多返回 32 条引用详情、单次最多修复 100 个文件，并使用协作式 60 秒扫描预算；第二个并发请求返回 `RESOURCE_EXHAUSTED`。修复不会等待语义和向量刷新完成。响应会返回跳过文件计数、截断状态、停止原因和实际生效的限制。重复值对应多个 Privacy 字段时报告 `ambiguous`，不会任意选取引用。

---

### list_privacy_categories()

列出当前用户下已有隐私配置的分类。

**HTTP API**

```
GET /api/v1/privacy-configs
```

```bash
curl -X GET http://localhost:1933/api/v1/privacy-configs \
  -H "X-API-Key: your-key"
```

**响应**

```json
{
  "status": "ok",
  "result": ["session", "skill"]
}
```

只返回已有配置的分类；Session 未提取到原值时，不会创建对应的 Privacy 配置。

---

### list_privacy_tree()

一次请求列出当前用户的分类、目标、当前生效版本号和字段元数据。
不返回历史版本或任何保存的值，没有 current 快照的目标会被跳过。

**HTTP API**

```text
GET /api/v1/privacy-configs/tree
```

可选查询参数 `category` 将结果限制在一个分类。非法标识符返回 `INVALID_ARGUMENT`。
空存量返回 `categories: []`；过滤的分类不存在时返回该分类和 `targets: []`。

```bash
curl "http://localhost:1933/api/v1/privacy-configs/tree?category=credential" \
  -H "X-API-Key: your-key"
```

**响应**

```json
{
  "status": "ok",
  "result": {
    "owner_user_id": "alice",
    "categories": [{
      "category": "credential",
      "targets": [{
        "target_key": "github",
        "version": 3,
        "fields": [{
          "field": "pat",
          "configured": true,
          "reference": "{{ov_privacy:alice:viking://~/privacy/credential/github#/values/pat}}"
        }]
      }]
    }]
  }
}
```

值为 `null` 或空字符串时，`configured` 为 false；其他值（包括 `false` 和 `0`）均为已配置。
API 始终返回绑定当前 owner 的 reference。CLI 的表格输出仅在设置 `--references`
时展示引用；JSON 输出保留全部元数据。

---

### list_privacy_targets()

列出分类下的 target_key。

**HTTP API**

```
GET /api/v1/privacy-configs/{category}
```

```bash
curl -X GET http://localhost:1933/api/v1/privacy-configs/skill \
  -H "X-API-Key: your-key"
```

**响应**

```json
{
  "status": "ok",
  "result": ["byted-viking-search-knowledgebase"]
}
```

---

### get_privacy_current()

获取 target 当前生效配置（`meta + current`）。

**HTTP API**

```
GET /api/v1/privacy-configs/{category}/{target_key}
```

```bash
curl -X GET "http://localhost:1933/api/v1/privacy-configs/skill/byted-viking-search-knowledgebase" \
  -H "X-API-Key: your-key"
```

**响应**

```json
{
  "status": "ok",
  "result": {
    "meta": {
      "category": "skill",
      "target_key": "byted-viking-search-knowledgebase",
      "active_version": 3,
      "latest_version": 5
    },
    "current": {
      "version": 3,
      "category": "skill",
      "target_key": "byted-viking-search-knowledgebase",
      "values": {
        "api_key": "***",
        "base_url": "https://example.com"
      }
    }
  }
}
```

> 若 target 不存在，返回 `NOT_FOUND`。

---

### list_privacy_fields()

列出当前生效字段的名称、配置状态和 owner-bound reference，不返回任何明文值。CLI 在 `reference` 或 `resolve` 的 category 形式省略字段时使用该接口：仅当 target 恰好有一个字段时自动推断；存在多个字段时列出可选项并要求显式指定。

```text
GET /api/v1/privacy-configs/{category}/{target_key}/fields
```

```json
{
  "status": "ok",
  "result": {
    "owner_user_id": "alice",
    "category": "credential",
    "target_key": "github-prod",
    "version": 1,
    "fields": [
      {
        "field": "pat",
        "configured": true,
        "reference": "{{ov_privacy:alice:viking://~/privacy/credential/github-prod#/values/pat}}"
      }
    ]
  }
}
```

---

### get_privacy_field()

获取当前生效版本中的单个字段。默认只返回带 owner 的引用和配置状态，不返回实际值；`reveal=true` 是供立即使用场景显式读取单字段的接口。

**HTTP API**

```text
GET /api/v1/privacy-configs/{category}/{target_key}/field?field={field}&reveal=false
```

默认响应：

```json
{
  "status": "ok",
  "result": {
    "owner_user_id": "alice",
    "category": "credential",
    "target_key": "github-prod",
    "field": "api_key",
    "version": 1,
    "configured": true,
    "reference": "{{ov_privacy:alice:viking://~/privacy/credential/github-prod#/values/api_key}}"
  }
}
```

传入 `reveal=true` 时，响应额外包含该字段的 `value`。接口不会返回同一 target 的其他字段。target 或 field 不存在时返回 `NOT_FOUND`。

也可以将 owner-bound reference 或字段 URI 传给统一解析接口：

```text
POST /api/v1/privacy-configs/resolve
```

```json
{"selector":"{{ov_privacy:alice:viking://~/privacy/credential/github-prod#/values/api_key}}"}
```

字段 URI 支持 `viking://~/privacy/...#/values/...` 和显式 `viking://user/{user_id}/privacy/...#/values/...`。显式 owner 与当前身份不一致时返回 `PERMISSION_DENIED`。

OpenViking 生成、保存和返回的 canonical reference 始终不带空格。解析器兼容双左花括号后和双右花括号前的空格或 Tab，例如 <code>&#123;&#123; ov_privacy:alice:... &#125;&#125;</code>，解析后仍规范化为 <code>&#123;&#123;ov_privacy:alice:...&#125;&#125;</code>；owner、URI 和路径字段内部不允许插入空白。

---

### upsert_privacy_config()

写入新版本并将其设为当前生效版本。

**行为说明**

- `values` 完整替换该版本的键值；本次省略的 key 不会出现在新快照中
- 传入新 key 会直接写入（允许新增）
- 若与当前版本完全一致，则复用当前版本号，不新建版本

**HTTP API**

```
POST /api/v1/privacy-configs/{category}/{target_key}
```

**请求体**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| values | object | 是 | - | 隐私配置键值 |
| change_reason | string | 否 | "" | 变更原因 |
| labels | object | 否 | null | 元信息标签 |

```bash
curl -X POST "http://localhost:1933/api/v1/privacy-configs/skill/byted-viking-search-knowledgebase" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "values": {
      "api_key": "secret-2",
      "base_url": "https://example.com",
      "region": "cn"
    },
    "change_reason": "rotate key",
    "labels": {
      "env": "prod"
    }
  }'
```

**响应**

```json
{
  "status": "ok",
  "result": {
    "version": 4,
    "category": "skill",
    "target_key": "byted-viking-search-knowledgebase",
    "values": {
      "api_key": "secret-2",
      "base_url": "https://example.com",
      "region": "cn"
    },
    "change_reason": "rotate key"
  }
}
```

---

### patch_privacy_config()

在服务端 target 锁内将 `values` 合并到当前配置。target 不存在时以空对象为基础创建版本 1；target 已存在时保留未涉及的字段并创建新版本。读取、合并和写入在同一把锁内完成，并发更新不同字段不会因客户端 read-modify-write 丢失。

```text
PATCH /api/v1/privacy-configs/{category}/{target_key}
```

```json
{
  "values": {"pat": "github_pat_..."},
  "change_reason": "configure GitHub PAT"
}
```

CLI 规范参数 `--field` 与 `--field-*` 使用该接口，不在 CLI 中预读 current；`--key` 与 `--key-*` 继续作为兼容别名：

```bash
ov privacy upsert credential github --field-pat 'github_pat_...'
```

---

### delete_privacy_config()

删除一个 target 的当前值、元信息和完整版本历史。重复删除是安全的：目标不存在时返回 `deleted: false`。

```text
DELETE /api/v1/privacy-configs/{category}/{target_key}
```

```json
{
  "status": "ok",
  "result": {
    "category": "credential",
    "target_key": "github-prod",
    "deleted": true
  }
}
```

字段删除只影响当前生效配置，对外不要求指定版本。删除后仍有字段时，实现会发布一个移除该字段的新 active 快照，已有历史版本继续保留；删除最后一个字段时会直接删除整个 target，包括当前值、元信息和全部历史，并返回 `target_deleted: true`。

```text
DELETE /api/v1/privacy-configs/{category}/{target_key}/field?field={field}
```

target URI、字段 URI 和 owner-bound reference 也可以通过以下接口删除：

```text
DELETE /api/v1/privacy-configs/selector
```

```json
{"selector":"viking://~/privacy/credential/github-prod#/values/api_key"}
```

---

### list_privacy_versions()

列出 target 的所有版本号。

**HTTP API**

```
GET /api/v1/privacy-configs/{category}/{target_key}/versions
```

```bash
curl -X GET "http://localhost:1933/api/v1/privacy-configs/skill/byted-viking-search-knowledgebase/versions" \
  -H "X-API-Key: your-key"
```

**响应**

```json
{
  "status": "ok",
  "result": [1, 2, 3, 4]
}
```

> 若 target 不存在，返回 `NOT_FOUND`。

---

### get_privacy_version()

获取某个历史版本详情。

**HTTP API**

```
GET /api/v1/privacy-configs/{category}/{target_key}/versions/{version}
```

```bash
curl -X GET "http://localhost:1933/api/v1/privacy-configs/skill/byted-viking-search-knowledgebase/versions/2" \
  -H "X-API-Key: your-key"
```

**响应**

```json
{
  "status": "ok",
  "result": {
    "version": 2,
    "category": "skill",
    "target_key": "byted-viking-search-knowledgebase",
    "values": {
      "api_key": "secret-1",
      "base_url": "https://example.com"
    }
  }
}
```

> 若 target/version 不存在，返回 `NOT_FOUND`。

---

### activate_privacy_version()

切换当前生效版本。

**HTTP API**

```
POST /api/v1/privacy-configs/{category}/{target_key}/activate
```

**请求体**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| version | int | 是 | 要激活的版本号 |

```bash
curl -X POST "http://localhost:1933/api/v1/privacy-configs/skill/byted-viking-search-knowledgebase/activate" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"version": 2}'
```

**响应**

```json
{
  "status": "ok",
  "result": {
    "version": 2,
    "category": "skill",
    "target_key": "byted-viking-search-knowledgebase",
    "values": {
      "api_key": "secret-1",
      "base_url": "https://example.com"
    }
  }
}
```

> 若 target/version 不存在，返回 `NOT_FOUND`。

---

## CLI 快速操作

```bash
# 分类/目标
ov privacy categories
ov privacy list skill
ov privacy list session

# 树形盘点当前目标和字段，不显示值
ov privacy tree
ov privacy tree --category credential --references
ov privacy tree --output json

# 当前生效配置（支持快捷形式）
ov privacy get skill byted-viking-search-knowledgebase
ov privacy skill byted-viking-search-knowledgebase

# 默认只取得 owner 绑定引用，不返回实际值；仅有一个字段时可省略 field
ov privacy reference credential github-prod
ov privacy reference credential github-prod api_key

# 显式读取当前生效版本中的一个字段，省略规则相同
ov privacy resolve credential github-prod
ov privacy resolve credential github-prod api_key
ov privacy resolve '{{ov_privacy:alice:viking://~/privacy/credential/github-prod#/values/api_key}}'
ov privacy resolve 'viking://~/privacy/credential/github-prod#/values/api_key'

# 更新（整包 JSON）
ov privacy upsert skill byted-viking-search-knowledgebase \
  --values-json '{"api_key":"secret-2","base_url":"https://example.com"}'

# 从本地 JSON 文件读取值，避免把值直接写入命令参数
ov privacy upsert skill byted-viking-search-knowledgebase --values-file ./privacy-values.json

# 更新单个 field；target 不存在时直接创建，存在时在服务端原子合并
ov privacy upsert skill byted-viking-search-knowledgebase \
  --field-api_key secret-3

# 版本查询与切换
ov privacy versions skill byted-viking-search-knowledgebase
ov privacy version skill byted-viking-search-knowledgebase 2
ov privacy activate skill byted-viking-search-knowledgebase 2

# 删除目标及全部版本；非交互环境必须显式 --yes
ov privacy delete credential github-prod --yes
ov privacy delete 'viking://~/privacy/credential/github-prod' --yes

# 删除当前字段；历史快照仍保留
ov privacy delete credential github-prod api_key --yes
ov privacy delete '{{ov_privacy:alice:viking://~/privacy/credential/github-prod#/values/api_key}}' --yes

# 存量内容扫描必须指定一个 URI，默认为 dry-run；--fix 才会改写
ov privacy scan --uri viking://~/resources/project-a
ov privacy scan --uri viking://~/resources/project-a --fix --node-limit 500
```

---

## 相关文档

- [技能](04-skills.md) - 技能写入与读取
- [会话](05-sessions.md) - 会话创建与隐私配置
- [隐私配置](../concepts/13-privacy.md) - 隐私敏感值的提取与还原机制
- [文件系统](03-filesystem.md) - `read`/`write`/`ls` 等
- [系统](07-system.md) - 服务状态与可观测性
