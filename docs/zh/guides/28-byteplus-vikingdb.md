# 自部署 OpenViking 连接 BytePlus VikingDB

此配置运行开源 OpenViking 服务，并使用 BytePlus VikingDB 存储向量和生成 Embedding。
如果连接的是 BytePlus 托管 OpenViking 实例，请使用
[BytePlus 托管 OpenViking 配置指南](27-byteplus-setup.md)。

## 端点与凭证

BytePlus 柔佛地域使用 `ap-southeast-1`，管理面和数据面使用不同的主机名：

| 用途 | 主机名 |
| --- | --- |
| Collection 与 Index 管理 | `vikingdb.ap-southeast-1.byteplusapi.com` |
| 数据写入、检索与 Embedding | `api-vikingdb.vikingdb.ap-southeast-1.bytepluses.com` |

连接 BytePlus 时必须同时设置 `console_host` 和 `data_host`。省略其中任意一个，
对应的请求会发往火山引擎的柔佛地域端点（`ap-southeast-1`）。

使用 `volcengine` 向量后端，通过 `console_host` 和 `data_host` 分别覆盖 AK/SK 端点。
省略这两个字段时，仍使用现有的火山引擎地域端点。填写不含协议或路径的主机名。
首尾空白和末尾斜杠会被移除。现有 `host` 字段仍用于 API Key 数据面访问，
不替代这两个 AK/SK 端点字段。

将以下配置合并到服务端的 `ov.conf`：

```json
{
  "storage": {
    "workspace": "./data",
    "vectordb": {
      "backend": "volcengine",
      "name": "openviking_context",
      "index_name": "context_index",
      "project": "default",
      "volcengine": {
        "region": "ap-southeast-1",
        "console_host": "vikingdb.ap-southeast-1.byteplusapi.com",
        "data_host": "api-vikingdb.vikingdb.ap-southeast-1.bytepluses.com",
        "ak": "<access-key>",
        "sk": "<secret-key>",
        "session_token": "<sts-token>"
      }
    }
  },
  "embedding": {
    "dense": {
      "provider": "vikingdb",
      "model": "bge-m3",
      "input": "text",
      "dimension": 1024,
      "region": "ap-southeast-1",
      "host": "api-vikingdb.vikingdb.ap-southeast-1.bytepluses.com",
      "ak": "<access-key>",
      "sk": "<secret-key>",
      "session_token": "<sts-token>"
    }
  }
}
```

临时 STS 凭证必须在两个配置区段都提供 `session_token`；长期凭证可省略该字段。
临时凭证过期时，应一起替换 AK、SK 和 Token。OpenViking 不会自动申请新 STS 凭证。
Embedding 的每个 credential 条目可提供自己的 Token；使用不同 AK/SK 时必须提供匹配的 Token。

模型需已在账户中开通，向量维度必须与 Collection 一致。当前模型列表和端点见
[BytePlus SDK 文档](https://docs.byteplus.com/en/docs/VikingDB/sdk_installation_and_initializationv2)。

## 验证

分别验证管理面请求、Embedding 和数据面请求。Collection 列表查询成功只证明管理面可访问。
在专用测试 Collection 中写入一个向量，按 ID 读取，通过测试 Index 搜索，然后删除测试资源。
Index 创建和更新需要时间才能提供搜索结果。

这些检查使用 VikingDB 凭证。文档摘要和记忆提取还需要单独配置 `vlm`。
完整导入流程应验证摘要、Embedding、向量存储、语义检索和原文读取。
