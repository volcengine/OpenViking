# OpenViking Assets Resolver

OpenViking Assets Resolver 用于解析并校验
[`openviking-assets/1`](../guides/18-openviking-assets.md) Catalog 与 Manifest，
返回可供客户端执行的标准化资产计划。它不会克隆仓库、创建资源或启动同步任务。

通常应直接使用 `openviking assets create`、`openviking assets sync` 或
`openviking assets watch`；这些命令会自动调用本接口。只有在开发自定义客户端时，
才需要直接请求 Resolver。

## 解析 Catalog 与 Manifest

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
| `manifest_yaml` | string | 是 | — | Manifest 的完整 YAML 内容，长度为 1～1,000,000 字符 |
| `catalog_yaml` | string | 是 | — | Catalog 的完整 YAML 内容，长度为 1～4,000,000 字符 |
| `manifest_label` | string | 否 | `manifest.yaml` | Manifest 的来源标签，用于错误信息，长度为 1～1,024 字符 |
| `catalog_label` | string | 否 | `assets.yaml` | Catalog 的来源标签，用于错误信息，长度为 1～1,024 字符 |

示例：

```bash
curl -X POST "${OPENVIKING_BASE_URL}/api/v1/openviking-assets/resolve" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${OPENVIKING_API_KEY}" \
  --data-binary @- <<'JSON'
{
  "manifest_yaml": "protocol: openviking-assets/1\ncatalog: assets.yaml\nassets:\n  - name: openviking\n",
  "catalog_yaml": "protocol: openviking-assets/1\nassets:\n  openviking:\n    connector: git\n    repo_url: https://github.com/volcengine/OpenViking\n    branch: main\n    watch_interval: 1440\n",
  "manifest_label": "manifests/code-qa.yaml",
  "catalog_label": "assets.yaml"
}
JSON
```

### 成功响应

```json
{
  "status": "ok",
  "result": {
    "protocol": "openviking-assets/1",
    "manifest": "manifests/code-qa.yaml",
    "catalog": "assets.yaml",
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

### 错误响应

协议或内容校验失败时返回 HTTP `400`，错误码为 `INVALID_ARGUMENT`。常见原因包括：

- YAML 无法解析或包含未知字段；
- `protocol` 不是 `openviking-assets/1`；
- Manifest 使用了 v1 尚不支持的非空 `include`；
- Manifest 引用了 Catalog 中不存在的资产；
- 连接器、仓库 URL、Git 引用或资产身份不合法；
- 同一份 Manifest 中出现重复资产身份。

请求字段为空、类型错误或超过长度限制时，由请求模型返回 HTTP `422`。

## 相关文档

- [OpenViking Assets 协议与运行指南](../guides/18-openviking-assets.md)
- [资源管理 API](02-resources.md)

