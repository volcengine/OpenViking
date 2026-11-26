# 隐私配置

隐私配置在当前用户空间内，按 `category + target_key` 管理技能、会话中的敏感信息（如密钥、凭据等）。配置值变化时会生成版本快照，可查询历史版本并切换生效版本。提交与当前版本相同的值不会创建新版本。
> 本页管理配置值和版本的接口不依赖于[用户 settings](08-admin.md#user-settings) 中的自动处理开关

以下示例使用当前用户的 API Key。trusted 模式需补充部署要求的身份头。读取值的接口和 CLI 命令会返回实际值；树总览和字段元数据接口不返回值。下文的 `***` 仅为示例占位符，不表示接口自动脱敏。

激活版本只改变 OpenViking 保存的配置，不会替外部服务轮换、撤销或恢复密钥。

修改同一用户下同一个 `category + target_key` 的配置需要获取目标写锁。锁被其他操作占用时，接口返回 HTTP 409（`CONFLICT`），不等待锁释放或自动重试本次修改。

## 典型场景

- 为某个 skill 或 session 保存密钥等敏感配置
- 轮换密钥（新版本）
- 回滚到历史版本
- 在读取 skill 或 session 内容时按占位符恢复配置值
- 把密钥挡在模型请求之外（见 [seal_privacy_texts()](#seal-privacy-texts)）

---

## 接口总览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/privacy-configs` | 列出隐私配置分类 |
| GET | `/api/v1/privacy-configs/tree` | 树形列出当前目标和字段元数据，不返回值 |
| POST | `/api/v1/privacy-configs/scan` | 扫描或修复已存储正文中的登记值 |
| POST | `/api/v1/privacy-configs/seal` | 把调用方提交的文本中已登记的密钥换成引用，可选登记新密钥 |
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

显式扫描一个已经持久化的文件或目录。默认 dry-run，不返回命中的原值；`fix=true` 时替换命中值，并异步刷新相应内容。

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
默认按照固定的顺序选择可还原的引用，也可以通过环境变量 `OPENVIKING_PRIVACY_AMBIGUOUS_STRICT=1` 来拒绝这种匹配操作。

---

### seal_privacy_texts()

把调用方提交的文本中、调用者已登记的值（`credential` 和 `auto` 分类中的值）换成规范引用，并可选地登记识别出的新 token，例如在文本发给模型厂商之前调用。结果不受用户隐私 `mode` 影响，`mode` 为 `off` 时同样生效。供网关、agent 插件等客户端使用。

```text
POST /api/v1/privacy-configs/seal
```

```json
{
  "texts": [
    {"text": "这是新的 GitHub token ghp_...，以后用它", "enroll": true},
    {"text": "（一段工具输出）"}
  ]
}
```

```json
{
  "status": "ok",
  "result": {
    "revision": "4f0c...-9a1b...",
    "texts": [
      {
        "text": "这是新的 GitHub token {{ov_privacy:alice:viking://~/privacy/auto/github-k3j2m8#/values/token}}，以后用它",
        "replaced": [
          {"start": 18, "end": 58, "reference": "{{ov_privacy:alice:viking://~/privacy/auto/github-k3j2m8#/values/token}}", "created": true, "temporary": true}
        ]
      },
      null
    ]
  }
}
```

**请求**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `texts` | array | 是 | 1–1024 项，所有文本的 UTF-8 大小合计不超过 16 MiB |
| `texts[].text` | string | 是 | 要封存的文本 |
| `texts[].enroll` | bool | 否（默认 `false`） | 同时登记识别出的、尚未登记的 token，见下文**自动登记** |

**响应**

| 字段 | 说明 |
|------|------|
| `result.revision` | 不透明字符串，每次写入 `credential` 或 `auto` 以及 `never_mask`、`custom_rules` 变化后都会改变，且已包含本次调用的登记。只用于相等比较，例如作为缓存键的一部分 |
| `result.texts[i]` | 为 `null` 表示该文本没有任何替换，调用方保留原文 |
| `result.texts[i].text` | 值已换成引用的文本 |
| `result.texts[i].replaced[].start`、`end` | 原文中的 Unicode 码点偏移，`end` 不含 |
| `result.texts[i].replaced[].reference` | 替换该片段的规范引用 |
| `result.texts[i].replaced[].temporary` | 当且仅当引用位于 `auto` 分类时为 true，即该值尚未以正式名字保存。它只取决于返回的 `revision` 时刻的保险库内容，调用方可以按该 revision 缓存 |
| `result.texts[i].replaced[].created` | 本次调用新登记的值，在请求内任何文本中的每次出现都为 true。它只是关于本次调用的统计，不要缓存它，也不要据此做决定 |

**匹配哪些值**

- 匹配规则与落盘路径一致：只匹配不短于 6 个字符的字符串值，排除 `never_mask`，跳过已有的 <code v-pre>{{ov_privacy:...}}</code> 片段，歧义处理也相同。
- 已登记的值包括 `credential` 分类中的值，以及存放自动登记值的 `auto` 分类中的值（见**自动登记**）。落盘路径的替换不看 `auto`。
- 与落盘路径不同，seal 还会匹配每个目标早先存储的各版本中的值，因此轮换后仍留在对话历史里的旧 token 会继续被替换。旧值换成其字段的引用，与当前值得到的引用相同。同一个值由多个字段持有时，`credential` 字段优先于 `auto` 字段；同一分类内，当前值优先于旧值。删除目标会一并删除其历史，其值随后不再被替换。

**自动登记**

- `enroll: true` 的文本中，内置探测规则和用户 `custom_rules` 识别出的、尚未登记的 token（排除 `never_mask`）会各自登记为 `auto` 分类中的一个新目标：名称为 `<规则名>-<随机后缀>`，字段为 `token`，label 为 `{"ov.rule": "<规则名>"}`，引用为 <code v-pre>{{ov_privacy:&lt;owner&gt;:viking://~/privacy/auto/&lt;target&gt;#/values/token}}</code>。
- 同一个值总是得到同一个引用，跨 worker 也一样。本次调用新登记的值，在请求内任何文本中的每次出现都返回 `created: true`。
- 单次调用最多登记 1024 个新值，超出时返回 `RESOURCE_EXHAUSTED`，且一个都不登记。登记是幂等的，超时的调用方直接重试即可。

要给自动登记的条目起正式名字，把它的引用作为值写入（见 [upsert_privacy_config()](#upsert-privacy-config)），`auto` 中的条目随后会被删除。除此之外，`auto` 中的条目会一直保留并不断累积；它们可以像其他目标一样列出、解析和删除。只有 seal 接口会写入 `auto`：以 `category=auto` 调用 upsert 或 patch 会以 `INVALID_ARGUMENT` 拒绝。

**错误**

| 情况 | 错误 |
|------|------|
| `texts` 少于 1 项或多于 1024 项 | `INVALID_ARGUMENT` |
| 所有文本的 UTF-8 大小合计超过 16 MiB | `RESOURCE_EXHAUSTED` |
| 需要登记的新值超过 1024 个 | `RESOURCE_EXHAUSTED`，一个都不登记 |
| 整个请求的替换总数超过 10000 | `RESOURCE_EXHAUSTED` |

- 出错时只返回错误，不会原样返回文本。
- 调用方应把任何非 `ok` 的结果（包括旧版本服务端返回的 HTTP 404）视为失败，不要转发原文。

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

只返回已有配置的分类，有过自动登记后也包括 `auto`；Session 未提取到原值时，不会创建对应的 Privacy 配置。

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
HTTP 接口和 `ov privacy tree` 列出全部分类；MCP 工具 `privacy_tree` 只有在以 `category="auto"` 调用时才列出 `auto` 分类。

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
- 顶层字符串值整串恰好是调用者自己 `credential` 或 `auto` 分类中某个字段的引用时，服务端写入该字段的当前值，而不是引用文本。目标不存在时相当于改名，目标已存在时相当于轮换并生成新版本。来源在 `auto` 中时，写入成功后会删除它（删除失败不影响本次写入）；来源在 `credential` 中时保留。写入目标不能位于 `auto`
- 请求中至少有一个值是这样的引用时，响应的 `values` 中每个已存储的值都换成该字段自己的规范引用，因此不会回显被复制的明文（`ov privacy upsert` 的表格和 JSON 输出也一样）。只含普通值的请求仍照旧返回存储的值
- 其他包含引用的顶层字符串值一律以 `INVALID_ARGUMENT` 拒绝：指向其他分类或其他用户、指向不存在的目标或字段，或嵌在更长字符串中；嵌套在对象或数组值中的引用不检查，按原样保存。PATCH、MCP `privacy_upsert` 工具和 CLI 同样适用
- 目标可以在 label `description` 中保存一段简短说明。`list_privacy_tree()` 会在带有说明的目标上返回它，其他 label 不在树中返回。MCP `privacy_upsert` 工具接受可选的 `description` 参数（最多 500 个字符），设置该 label 并保留其他 label；不传时 label 保持不变。CLI 可通过 `--labels-json '{"description":"..."}'` 设置

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

指向 `credential` 或 `auto` 字段的整串引用会和 upsert 一样被换成被引用字段的值，因此只持有引用的模型也能在看不到明文的情况下改名或轮换凭据。与 upsert 一样，此时响应中的每个值都显示为其字段的引用。例如，下面的请求体把一个自动登记的 token 保存为 `pat`：

```json
{"values": {"pat": "{{ov_privacy:alice:viking://~/privacy/auto/github-k3j2m8#/values/token}}"}}
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

### 使用 Privacy 引用运行外部命令

`ov privacy run` 支持调用方或 Agent 直接使用 Privacy 引用（<code v-pre>{{ov_privacy:...}}</code>，可通过 `ov privacy reference` 或 `ov privacy tree --references` 获取）来运行本地外部命令（如 `gh`、`curl`）。在启动外部命令前，`ov privacy run` 会先解析环境变量或参数中的引用并注入敏感值；外部命令输出到 `stdout`/`stderr` 的内容中，匹配的敏感值也会被实时替换为对应的 Privacy 引用。

> 输出替换仅作用于该外部命令的 `stdout` 和 `stderr`，不会处理其写入文件或发送到网络的内容。

```bash
# 1. 通过环境变量传入（推荐，避免敏感值出现在进程参数中）
ov privacy run --env GITHUB_TOKEN='{{ov_privacy:alice:viking://~/privacy/credential/github-prod#/values/api_key}}' -- gh api user

# 2. 从文件批量加载（每行一条 NAME={{ov_privacy:...}}，支持成对引号与 # 注释）
ov privacy run --env-file ./privacy.env -- gh api user

# 3. 直接嵌在外部命令的单个参数中
ov privacy run -- curl -H 'Authorization: Bearer {{ov_privacy:alice:viking://~/privacy/credential/github-prod#/values/api_key}}' https://api.example.com

# 4. 需要管道或重定向时显式调用 sh -c，并通过环境变量传入引用
ov privacy run --env GITHUB_TOKEN='{{ov_privacy:alice:viking://~/privacy/credential/github-prod#/values/api_key}}' -- \
  sh -c 'curl -sS -H "Authorization: Bearer $GITHUB_TOKEN" https://api.example.com | jq .login'
```

> 在上面的示例 3 和 4 中，敏感值最终都会进入 `curl` 的进程参数（示例 3 由 `ov privacy run` 替换参数中的引用，示例 4 由 `sh` 展开 `$GITHUB_TOKEN`），可能会被本机其他进程、其他容器或宿主机看到。因此，建议优先使用 `--env` / `--env-file` 让目标程序直接读取环境变量（如示例 1、2），也可以设置 `OPENVIKING_PRIVACY_RUN_STRICT=1` 禁止在 `ov privacy run` 的命令参数中使用引用。

---

## 相关文档

- [技能](04-skills.md) - 技能写入与读取
- [会话](05-sessions.md) - 会话创建与隐私配置
- [隐私配置](../concepts/13-privacy.md) - 隐私敏感值的提取与还原机制
- [文件系统](03-filesystem.md) - `read`/`write`/`ls` 等
- [系统](07-system.md) - 服务状态与可观测性
