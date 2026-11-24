# 服务角色与启停

`server.roles` 支持 `api`、`queue_worker`、`scheduler` 的任意非空组合，默认全部启用；`--roles` 可覆盖配置：

```bash
openviking-server --config ov.conf --roles api
openviking-server --config ov.conf --roles queue_worker --workers 2
openviking-server --config ov.conf --roles scheduler
```

| 角色 | 启动内容 |
| --- | --- |
| `api` | 业务 HTTP / MCP；按配置启用 Studio、OAuth、Gateway |
| `queue_worker` | 队列任务处理与到期清理 |
| `scheduler` | 已启用的 Watch、Compile、Session idle、归档规则清理扫描；到期任务发布 |

`--workers` 指定进程数，各 worker 加载相同角色。
各节点注册 `/health`、`/ready`，以及需要鉴权的
`/api/v1/system/status`、`/api/v1/system/wait`、`/api/v1/system/consistency`。
`/metrics` 按配置启用。其他路由和 MCP 要求 `api`，`--with-bot` 也要求 `api`。

- OAuth 或内嵌 Gateway（`gateway.enabled: true`）要求所有角色同进程部署，保证 Account/User 删除完整。
- 使用内置 VikingBot 的 Skill Compile 要求 `api` 和 `queue_worker` 同进程运行；拆分时，两端配置 `compile_api.base_url`，指向共同可访问的 Compile Runtime。

启动先准备存储和账户能力、恢复任务，再激活角色。
SIGINT/SIGTERM 先停生产者、排空消费者及写入，再关闭依赖；无法排空时保留依赖。
退出脚本须等待清理完成。

配置刷新、Task 维护和到期登记按配置在各节点运行。

含 `scheduler` 且启用 Redis 到期功能时，默认由父进程托管独立 Expiry publisher。
`expiry.publisher_separate_process=false` 指定进程内运行。

嵌入 Core 按 `initialize()` → 依赖接线 → `recover()` → `start_background()` 调用。
`create_app(service=...)` 接收尚未执行的服务，存储由调用方管理。
同一服务支持一次宿主 lifespan，启动与关闭须在同一 task 内完成。

多进程要求共享存储；本地存储使用独占锁。
scheduler 按模块协调，没有覆盖所有扫描任务的统一 leader。
