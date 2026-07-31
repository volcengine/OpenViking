# OpenViking Assets Resolver

OpenViking Assets Resolver 用于解析并校验
[`openviking-assets/1`](../guides/18-openviking-assets.md) Manifest——既支持在
`catalog` 字段中直接定义资产的单文件 Manifest，也支持搭配单独 Catalog 文件的
Manifest——并返回可供客户端执行的标准化资产计划。它不会克隆仓库、创建资源或启动
同步任务。

通常应直接使用 `ov add-resource --manifest <file>`；CLI 会自动调用 Resolver 和
权限预检接口。只有在开发自定义客户端时，才需要直接请求这些接口。

## 解析 Manifest

```http
POST /api/v1/openviking-assets/resolve
```

### 鉴权

接口沿用 OpenViking Server 的标准鉴权方式。启用 API Key 时，请在请求中传入：

```http
X-API-Key: <your-api-key>
```

### 请求体

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `manifest_yaml` | string | 是 | — | Manifest 的完整 YAML 内容，长度为 1～4,000,000 字符 |
| `catalog_yaml` | string | 否 | — | Catalog 的完整 YAML 内容，长度为 1～4,000,000 字符。Manifest 按名称选择资产时必填；Manifest 在 `catalog` 中定义资产时必须省略。 |
| `manifest_label` | string | 否 | `manifest.yaml` | Manifest 的来源标签，用于错误信息，长度为 1～1,024 字符 |
| `catalog_label` | string | 否 | `catalog.yaml` | Catalog 的来源标签，用于错误信息，长度为 1～1,024 字符 |

单文件 Manifest 示例：

```bash
curl -X POST "${OPENVIKING_BASE_URL}/api/v1/openviking-assets/resolve" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${OPENVIKING_API_KEY}" \
  --data-binary @- <<'JSON'
{
  "manifest_yaml": "protocol: openviking-assets/1\ncatalog:\n  - name: openviking\n    connector: git\n    watch_interval: 1440\n    params:\n      repo_url: https://github.com/volcengine/OpenViking\n      branch: main\n",
  "manifest_label": "manifest.yaml"
}
JSON
```

Manifest 按名称选择资产时，把 Catalog YAML 放入 `catalog_yaml`，来源标签放入
`catalog_label` 一并发送。

### 成功响应

```json
{
  "status": "ok",
  "result": {
    "protocol": "openviking-assets/1",
    "manifest": "manifest.yaml",
    "catalog": "manifest.yaml",
    "assets": [
      {
        "name": "openviking",
        "connector": "git",
        "repo_url": "https://github.com/volcengine/OpenViking",
        "branch": "main",
        "auth_ref": null,
        "watch_interval": 1440.0,
        "locator": "github.com/volcengine/OpenViking",
        "git_ref": "main",
        "asset_id": "a1b2c3d4e5f6"
      }
    ]
  }
}
```

其中：

- `locator` 是规范化后的仓库定位符。
- `git_ref` 是最终解析出的 Git 引用。
- `asset_id` 是由连接器、规范化定位符与 Git 引用生成的 12 位稳定标识；示例值仅作格式说明。
- `watch_interval` 的单位是分钟。
- `catalog` 回显 `catalog_label`；单文件 Manifest 时与 Manifest 标签相同。

### 错误响应

协议或内容校验失败时返回 HTTP `400`，错误码为 `INVALID_ARGUMENT`。常见原因包括：

- YAML 无法解析或包含未知字段；
- `protocol` 不是 `openviking-assets/1`，或 Manifest 定义了 `catalog` 却没有声明 `protocol`；
- Manifest 使用了 v1 尚不支持的非空 `include`；
- Manifest 定义了 `catalog`，请求却同时传入了 `catalog_yaml`；
- Manifest 按名称选择资产，请求却没有提供 `catalog_yaml`；
- Manifest 引用了 Catalog 中不存在的资产；
- 连接器、仓库 URL、Git 引用或资产身份不合法；
- 同一份 Manifest 中出现重复资产身份。

请求字段为空、类型错误或超过长度限制时，由请求模型返回 HTTP `422`。

## 预检 Git 仓库权限

```http
POST /api/v1/openviking-assets/preflight
```

该接口在 OpenViking Server 的实际运行环境执行只读 `git ls-remote`，校验仓库和可选 ref
是否可读。它不会克隆仓库、创建资源或启动任务。Manifest 模式在 dry-run 和正式提交之前
都会调用该接口。

### 请求体

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `name` | string | 是 | 资产名称 |
| `connector` | string | 是 | 当前必须是 `git` |
| `repo_url` | string | 是 | Git clone URL |
| `branch` | string | 否 | 要验证的 branch 或 tag；省略时验证远端 `HEAD` |
| `auth_config.username` | string | 否 | HTTP Basic 用户名，默认 `oauth2` |
| `auth_config.token` | string | 否 | 一次性 Git token，不持久化 |

```bash
curl -X POST "${OPENVIKING_BASE_URL}/api/v1/openviking-assets/preflight" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${OPENVIKING_API_KEY}" \
  -d '{
    "name": "private-repository",
    "connector": "git",
    "repo_url": "https://github.com/example/private-repository",
    "branch": "main",
    "auth_config": {
      "username": "oauth2",
      "token": "<github-token>"
    }
  }'
```

显式传入 token 时，preflight 不会回退到服务端 Git credential helper。token 通过子进程
环境传递，不出现在 Git 命令参数和响应中。

### 成功响应

```json
{
  "status": "ok",
  "result": {
    "name": "private-repository",
    "connector": "git",
    "locator": "github.com/example/private-repository",
    "git_ref": "main",
    "accessible": true
  }
}
```

### 错误响应

| HTTP 状态 | 错误码 | 说明 |
| --- | --- | --- |
| `403` | `PERMISSION_DENIED` | 仓库不存在、凭据无效或当前身份没有读取权限 |
| `404` | `NOT_FOUND` | 仓库可访问，但指定 branch/tag 不存在 |
| `503` | `UNAVAILABLE` | DNS、连接或 Git 可执行文件不可用 |
| `504` | `DEADLINE_EXCEEDED` | 权限预检超过 15 秒 |

## 相关文档

- [OpenViking Assets 协议与运行指南](../guides/18-openviking-assets.md)
- [资源管理 API](02-resources.md)
