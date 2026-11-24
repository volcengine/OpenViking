# Dedicated 与 Serverless 运行模式

`ov.conf` 顶层的 `runtime_mode` 可选 `dedicated`（默认）或 `serverless`。
请在部署新实例时选择运行模式。已有实例不支持原地切换模式：修改配置并重启不会迁移存量 Account
或其向量数据。Account / Cluster 动态配置接口不能修改运行模式。

`server.roles` 独立选择 API、Queue Worker 和 Scheduler，各节点应使用相同的运行模式。
不含 `api` 的节点保留系统接口和监控端点，包括 `/health`、`/ready`；
启动命令、角色职责和退出要求见[服务角色与启停](../configuration/03-server-roles.md)。

| 行为 | dedicated | serverless |
| --- | --- | --- |
| 集群业务模型和向量配置 | 延续现有账户覆盖和集群回退规则 | 可省略，各业务 Account 自带配置 |
| 默认本地 Embedding / VectorDB | 保留 | 不补本地模型，不构造共享向量 adapter |
| `default` 账号 | 沿用原有初始化逻辑 | 启动时不自动创建，保留已有账号 |
| 默认 context Collection 和身份工作区 | 启动时初始化 | 启动时跳过，业务工作区在创建 Account 时初始化 |
| 系统资产同步 | 保留 | 保留 AGFS 静态文件复制 |

## 共享实例启动

共享存储、账户/任务存储、队列、锁和凭证需另行配置；`serverless` 不会让本地后端自动支持分布式部署。

```json
{
  "runtime_mode": "serverless",
  "server": {
    "roles": ["api"],
    "auth_mode": "api_key",
    "root_api_key": "<ROOT Key>"
  }
}
```

共享实例可在没有业务 Account、未配置全局 `embedding`、`vlm`、`storage.vectordb` 时启动，
Worker / Scheduler 也遵循相同边界。

`/ready` 检查服务初始化、共享 AGFS 访问、APIKeyManager 和 Embedding provider 初始化；
serverless 跳过全局 VectorDB 和 Ollama 探测。

## 创建业务 Account

ROOT 调用 `POST /api/v1/admin/accounts` 时必须同时提供完整的
`settings.vlm`、`settings.embedding` 和 `settings.vectordb`。
缺失或维度冲突会在创建账户前拒绝。

```json
{
  "account_id": "library-a",
  "admin_user_id": "admin",
  "settings": {
    "vlm": {
      "model": "<VLM model>",
      "credentials": [
        {"provider": "openai", "api_key": "<VLM Key>", "api_base": "<VLM endpoint>"}
      ]
    },
    "embedding": {
      "dense": {
        "model": "<Embedding model>",
        "dimension": 768,
        "credentials": [
          {"provider": "openai", "api_key": "<Embedding Key>", "api_base": "<Embedding endpoint>"}
        ]
      }
    },
    "vectordb": {
      "backend": "http",
      "url": "<VectorDB endpoint>",
      "name": "library-a",
      "index_name": "default",
      "dimension": 768
    }
  }
}
```

Account 向量后端支持 `http`、`volcengine` 和 `vikingdb`。远端 Collection / Index 须在创建
Account 前准备好，该接口不申请外部向量资源。模型、凭证和向量绑定来自各自的 Account，
缺配置时不会回退到集群业务身份或其他 Account。Query Planner 未单独配置时使用该 Account 的 VLM。
既有创建期字段和 PATCH 规则不变，见[Runtime Configuration](../api/08-admin.md#runtime-configuration)。
