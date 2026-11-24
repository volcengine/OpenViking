# Dedicated and Serverless Runtime Modes

`ov.conf` accepts `runtime_mode: dedicated` (default) or `serverless`.
Choose the mode when deploying a new instance. Changing modes in place on an
existing instance is unsupported: restarting with a different mode does not
migrate existing Accounts or their vector data. Account and Cluster configuration
APIs cannot change the mode.

`server.roles` independently selects API, Queue Worker, and Scheduler duties.
Nodes should use the same runtime mode. Non-API nodes retain system and metrics
endpoints, including `/health` and `/ready`; see
[Server Roles and Lifecycle](../configuration/03-server-roles.md) for startup and shutdown.

| Behavior | dedicated | serverless |
| --- | --- | --- |
| Cluster business models and vector configuration | Existing account overrides and cluster fallback | Optional; each business Account supplies its own settings |
| Default local Embedding / VectorDB | Preserved | No synthesized local model or shared vector adapter |
| `default` Account | Bootstrapped as before | Not created on startup; existing Accounts are kept |
| Default context Collection and identity workspace | Initialized | Skipped on startup; workspaces are initialized when Accounts are created |
| System asset synchronization | Preserved | Preserved as static AGFS file copies |

## Starting a Shared Instance

Configure shared storage, stores, queues, locks, and credentials separately.
Serverless mode does not make local backends distributed.

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

A shared instance can start with no business Accounts and without global
`embedding`, `vlm`, or `storage.vectordb`, including on workers and schedulers.

`/ready` checks service initialization, shared AGFS access, APIKeyManager and
Embedding provider initialization; serverless skips global VectorDB and Ollama
checks.

## Creating a Business Account

ROOT requests to `POST /api/v1/admin/accounts` must supply complete
`settings.vlm`, `settings.embedding`, and `settings.vectordb`.
Missing settings and dimension conflicts are rejected before account creation.

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

Account vector backends support `http`, `volcengine`, and `vikingdb`.
Provision remote Collections and Indexes before creating Accounts; this API
does not allocate them. Each Account owns its models, credentials, and vector
bindings, with no fallback to Cluster business identity or another Account.
An unspecified Query Planner uses the Account's VLM. Existing create-only and
PATCH rules still apply; see [Runtime Configuration](../api/08-admin.md#runtime-configuration).
