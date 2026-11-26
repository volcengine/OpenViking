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
| `queue_worker` | Queue tasks and expiry cleanup |
| `scheduler` | Enabled Watch, Compile, Session idle, and rule-cleanup scans; expiry publishing |

`--workers` sets the process count, with the same roles in every worker.
Every node registers `/health`, `/ready`,
and the authenticated `/api/v1/system/status`, `/api/v1/system/wait`,
`/api/v1/system/consistency`; `/metrics` is enabled through its configuration.
Other routes and MCP require `api`; `--with-bot` also requires `api`.

- OAuth and embedded Gateway (`gateway.enabled: true`) require all roles in the same process to keep Account/User deletion complete.
- Skill Compile with the built-in VikingBot requires `api` and `queue_worker` in the same process. For separate roles, configure `compile_api.base_url` on both to reach a shared Compile Runtime.

Startup prepares storage and account capabilities, recovers tasks, then activates roles.
SIGINT/SIGTERM stops producers, drains consumers and writes, then closes dependencies;
failed draining retains dependencies. Shutdown scripts must wait for cleanup.

Configuration refresh, Task maintenance, and expiry registration run on each node as configured.

With `scheduler` and Redis expiry enabled, the parent manages an Expiry publisher.
`expiry.publisher_separate_process=false` runs it inside the service process.

Embedded Core usage follows `initialize()` → dependency wiring → `recover()` → `start_background()`.
`create_app(service=...)` accepts an inactive service; the caller owns its storage.
One service supports one host lifespan, entered and exited in the same task.

Multiple processes require shared stores; local storage uses an exclusive lock.
Schedulers coordinate per module, without a common leader for all scans.
