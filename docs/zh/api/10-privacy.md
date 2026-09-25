# 隐私配置（Privacy Configs）

隐私配置用于按 `category + target_key` 管理敏感字段版本（如 skill 的 `api_key`、`base_url`）。

配置值变化时会生成版本快照，可查询历史版本并切换生效版本。提交与当前版本相同的值不会创建新版本。

以下示例使用当前用户的 API Key。trusted 模式需补充部署要求的身份头。读取接口和 CLI 会返回保存的实际值；下文的 `***` 仅为示例占位符，不表示接口自动脱敏。

激活版本只改变 OpenViking 保存的配置，不会替外部服务轮换、撤销或恢复密钥。

## 典型场景

- 为某个 skill 保存密钥等敏感配置
- 轮换密钥（新版本）
- 回滚到历史版本
- 在读取 skill 内容时按占位符自动恢复配置值

---

## 接口总览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/privacy-configs` | 列出隐私配置分类 |
| GET | `/api/v1/privacy-configs/{category}` | 列出分类下目标 |
| GET | `/api/v1/privacy-configs/{category}/{target_key}` | 获取当前生效配置（meta + current） |
| POST | `/api/v1/privacy-configs/{category}/{target_key}` | 写入新版本并激活 |
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

---

## API 参考

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
  "result": ["skill"]
}
```

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

# 当前生效配置（支持快捷形式）
ov privacy get skill byted-viking-search-knowledgebase
ov privacy skill byted-viking-search-knowledgebase

# 更新（整包 JSON）
ov privacy upsert skill byted-viking-search-knowledgebase \
  --values-json '{"api_key":"secret-2","base_url":"https://example.com"}'

# 从本地 JSON 文件读取值，避免把值直接写入命令参数
ov privacy upsert skill byted-viking-search-knowledgebase --values-file ./privacy-values.json

# 仅更新部分 key（先读取 current 再合并）
ov privacy upsert skill byted-viking-search-knowledgebase \
  --key-api_key secret-3

# 版本查询与切换
ov privacy versions skill byted-viking-search-knowledgebase
ov privacy version skill byted-viking-search-knowledgebase 2
ov privacy activate skill byted-viking-search-knowledgebase 2
```

---

## 相关文档

- [技能](04-skills.md) - 技能写入与读取
- [文件系统](03-filesystem.md) - `read`/`write`/`ls` 等
- [系统](07-system.md) - 服务状态与可观测性
