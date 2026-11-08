# Dedicated 与 Serverless 运行模式

`ov.conf` 顶层的 `runtime_mode` 决定业务配置和初始化边界。可选值为
`dedicated` 和 `serverless`，默认 `dedicated`。未知值启动报错。
模式在启动时确定，Account / Cluster 动态配置接口不能修改它。
修改配置文件后需重启进程。

`server.roles` 独立决定 API、Queue Worker 和 Scheduler 的执行职责。
同一部署的各角色应使用相同的 `runtime_mode`。

| 行为 | dedicated | serverless |
| --- | --- | --- |
| 集群业务模型和向量配置 | 延续现有账户覆盖和集群回退规则 | 可省略，各业务 Account 自带配置 |
| 默认本地 Embedding / VectorDB | 保留 | 不补本地模型，不构造共享向量 adapter |
| 默认 context Collection | 初始化 | 跳过 |
| 内部默认身份目录 | 完整初始化 | 仅准备文件目录，不读取向量或登记 Embedding 消息 |
| 系统资产同步 | 保留 | 保留 AGFS 静态文件复制 |
| `/ready` | 延续现有探测 | 检查公共基础设施和 providers，不探测业务模型或 Collection |

## 共享实例启动

下面的片段只说明模式和角色；公共 AGFS、账户/任务/Watch 存储、
队列、锁、鉴权、加密和配置来源仍需按部署配置。
多进程共享运行须使用相应的共享存储和缓存，`serverless` 不会代替这些基础设施。

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

省略 `embedding`、`vlm`、`storage.vectordb` 的共享实例可在没有业务 Account
时启动。Worker / Scheduler 也遵循相同边界。内部默认身份继续支持管理和文件操作，
但不能在缺少自身业务配置时使用模型或向量空间。

`/health` 保持进程级健康语义。Serverless `/ready` 检查 AGFS、账户和任务存储、
QueueFS、PathLock 及配置/模型/向量 providers 的准备状态。
公共依赖失败返回 503；某个 Account 的模型或向量后端不可达不影响基础 readiness。

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

Account 向量后端支持现有的 `http`、`volcengine` 和 `vikingdb` 配置。
远端 Collection / Index 由业务建库流程预先准备，CreateAccount 不申请外部向量资源。
完整连接参数和凭证须来自该 Account；缺配置时不会借用集群或其他 Account 的业务身份。
并发、超时、重试等普通运行参数继续使用现有默认值。
Query Planner 未单独配置时使用该 Account 的 VLM。

创建顺序保持为：校验 settings → 创建账户及默认用户 → 持久化 settings →
初始化 workspace。不同 Account 可以使用不同模型、凭证、Collection 和维度。
既有 create-only 字段和动态 PATCH 边界继续生效。
Worker 从持久任务恢复 Account 身份后解析该 Account 的配置。

专属实例无需修改现有配置。部署方应明确下发模式；内核不会根据客户产品版本推导模式。
