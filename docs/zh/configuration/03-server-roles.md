# 服务角色与启停

`server.roles` 支持 `api`、`queue_worker`、`scheduler` 的任意非空组合，默认全部启用：

```json
{"server": {"roles": ["api", "queue_worker", "scheduler"], "workers": 1}}
```

也可以通过命令行覆盖：

```bash
openviking-server --config ov.conf --roles api
openviking-server --config ov.conf --roles queue_worker --workers 2
openviking-server --config ov.conf --roles scheduler
openviking-server --config ov.conf --roles api,queue_worker
```

| 角色 | 启动内容 |
| --- | --- |
| `api` | 业务 HTTP / MCP；按配置启用 Studio、OAuth、Gateway |
| `queue_worker` | QueueManager 消费者、Expiry consumer、TTL maintenance |
| `scheduler` | 已启用的 Watch、Compile 补偿、Session 文件 idle、归档规则清理扫描；托管 Expiry publisher |

所有角色均使用 `openviking-server` 和 Uvicorn，`--workers` 指定服务进程数，同一次启动的 worker 加载相同角色。
各节点注册系统接口和按配置启用的 `/metrics`：`/health`、`/ready`，以及需要鉴权的
`/api/v1/system/status`、`/api/v1/system/wait`、`/api/v1/system/consistency`。
不含 `api` 时不注册其他路由，也不启动 MCP。业务流量只路由到 API 节点，`--with-bot` 需要 `api`。

- 启用 OAuth 或内嵌 Gateway（`gateway.enabled: true`）时，不支持按角色拆分部署。请保留默认的三个角色同进程运行，不要为同一实例单独部署 Worker，否则删除账户或用户时可能遗漏 OAuth 授权或 Gateway 数据。
- 使用 Skill Compile 时，`--with-bot` 启动的内置 VikingBot 要求 `api` 和 `queue_worker` 在同一进程运行。若拆分角色，在两端配置 `compile_api.base_url`，指向两端均可访问的 Compile Runtime。

启动先准备存储和账户能力、恢复任务，再启动对应角色。SIGINT/SIGTERM 触发同一个 lifespan 清理：
先停生产者，排空消费者及写入，最后关闭宿主依赖和存储；无法排空时抛出错误并保留依赖。
非 API 节点的退出脚本应等待信号触发的清理完成；这些节点仍不提供 `/api/v1/tasks`。

配置/账户缓存刷新、Task 维护和到期登记等进程级任务按配置在各节点运行，不统一归到 scheduler。

含 `scheduler` 且启用 Redis 到期功能时，默认由父进程托管独立 Expiry publisher。
设置 `expiry.publisher_separate_process=false` 后改为进程内运行，仍使用现有分片协调。

直接嵌入 Core 时，调用 `initialize()` 后需完成依赖接线，再调用 `recover()`、`start_background()`；
公开 HTTP SDK 的初始化行为不变。

测试注入 `create_app(service=...)` 时，service 必须尚未启动后台执行；app 管理本次执行，存储由调用方关闭。
同一 service 不支持多个运行中的 app 或关闭后重启，启动与关闭须在同一个 lifespan task 内完成。

多进程必须使用支持共享的 Task、队列、账户和文件存储；本地存储独占锁仍保留。
多个 scheduler 沿用各模块的协调机制，没有覆盖所有扫描任务的统一 leader。
