# Server Roles and Lifecycle

`server.roles` accepts any nonempty combination of `api`, `queue_worker`, and
`scheduler`, with all three enabled by default. `--roles` overrides the configuration:

```bash
openviking-server --config ov.conf --roles api
openviking-server --config ov.conf --roles queue_worker --workers 2
openviking-server --config ov.conf --roles scheduler
```

| Role | Runs |
| --- | --- |
| `api` | Business HTTP / MCP; Studio, OAuth, and Gateway when configured |
| `queue_worker` | QueueManager consumers, Expiry consumer, TTL maintenance |
| `scheduler` | Enabled Watch, Compile, Session idle, and rule-cleanup scans; managed Expiry publisher |

All roles use `openviking-server` and Uvicorn; `--workers` sets the process count,
with the same roles in every worker. Every node registers `/health`, `/ready`,
and the authenticated `/api/v1/system/status`, `/api/v1/system/wait`,
`/api/v1/system/consistency`; `/metrics` is enabled through its configuration.
Non-API nodes register no other routes and do not start MCP.
Route business traffic to API nodes; `--with-bot` requires `api`.

- OAuth uses a local SQLite store. When OAuth is enabled, run `api` and `queue_worker` together so account and user deletion can revoke grants in that store.
- For Skill Compile, the built-in VikingBot started by `--with-bot` requires `api` and `queue_worker` in the same process. If the roles run separately, configure `compile_api.base_url` on both to point to a Compile Runtime they can reach.

Startup prepares storage and account capabilities, recovers tasks, then activates roles.
SIGINT/SIGTERM stops producers, drains consumers and writes, then closes dependencies.
Failed draining retains required dependencies. Non-API shutdown scripts must await
signal-driven cleanup; `/api/v1/tasks` remains unavailable on these nodes.

Configuration/account refresh, Task maintenance, and expiry registration run on
each node as configured, independently of the scheduler role.

With `scheduler` and Redis expiry enabled, the parent manages an Expiry publisher.
`expiry.publisher_separate_process=false` runs it inside the service process.

Core embedding requires `initialize()`, dependency wiring, `recover()`, then
`start_background()`; HTTP SDK initialization is unchanged. An injected service
must have inactive execution and caller-owned storage. Active app sharing and
close/restart are unsupported; enter and exit the host lifespan in the same task.

Multiple processes require shared stores; local storage retains its exclusivity lock.
Schedulers use each module's coordination, with no common leader for all scans.
