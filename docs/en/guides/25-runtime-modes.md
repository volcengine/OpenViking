# Dedicated and Serverless Runtime Modes

The top-level `runtime_mode` in `ov.conf` controls business configuration and
initialization. Supported values are `dedicated` and `serverless`; the default is
`dedicated`. Unknown values fail startup. Account and Cluster dynamic
configuration APIs cannot change the mode. Restart after changing it in the
configuration file.

`server.roles` independently selects API, Queue Worker, and Scheduler duties.
All roles in one deployment should use the same runtime mode.

| Behavior | dedicated | serverless |
| --- | --- | --- |
| Cluster business models and vector configuration | Existing account overrides and cluster fallback | Optional; each business Account supplies its own settings |
| Default local Embedding / VectorDB | Preserved | No synthesized local model or shared vector adapter |
| Default context Collection | Initialized | Skipped |
| Internal default identity directories | Full initialization | Files only; no vector reads or Embedding messages |
| System asset synchronization | Preserved | Preserved as static AGFS file copies |
| `/ready` | Existing probes | Shared infrastructure and prepared providers; no business model or Collection probes |

## Starting a Shared Instance

This fragment illustrates mode and role selection. Configure shared AGFS,
Account/Task/Watch stores, queues, locks, authentication, encryption, and the
configuration source for your deployment. Serverless mode does not provision
these dependencies or make local stores distributed.

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

A shared instance can start with zero business Accounts and without `embedding`,
`vlm`, or `storage.vectordb`. Workers and schedulers follow the same boundary.
The internal default identity supports management and file operations but cannot
use business models or vectors without its own settings.

`/health` retains process health semantics. Serverless `/ready` checks AGFS,
Account and Task stores, QueueFS, PathLock, and prepared configuration/model/vector
providers. Shared dependency failures return 503. A failed Account model or
vector backend does not affect infrastructure readiness.

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

Account vector backends support the existing `http`, `volcengine`, and `vikingdb`
settings. Provision the remote Collection and Index through your business
provisioning workflow first; CreateAccount does not allocate external vector
resources. Connection settings and credentials belong to the Account. Missing
settings never fall back to cluster business identity or another Account.
Ordinary concurrency, timeout, and retry defaults remain available.
An unspecified Query Planner uses the Account's VLM.

Creation keeps this order: validate settings → create Account and default user →
persist settings → initialize workspace. Accounts may use different models,
credentials, Collections, and dimensions. Existing create-only fields and dynamic
PATCH restrictions still apply. Recovered worker tasks resolve configuration
using their persisted Account identity.

Dedicated deployments can retain their existing configuration. Deployment
systems select the mode explicitly; the kernel does not infer it from product
edition.
