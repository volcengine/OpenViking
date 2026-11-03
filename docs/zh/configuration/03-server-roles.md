# 服务角色与启停

`server.roles` 控制当前部署启动哪些执行职责，默认全部启用：

```json
{
  "server": {
    "roles": ["api", "queue_worker", "scheduler"],
    "workers": 1
  }
}
```

也可以通过命令行覆盖：

```bash
openviking-server --config ov.conf --roles api
openviking-server --config ov.conf --roles queue_worker --workers 2
openviking-server --config ov.conf --roles scheduler
openviking-server --config ov.conf --roles api,queue_worker,scheduler
```

| 角色 | 启动内容 |
| --- | --- |
| `api` | HTTP、MCP 和已有 API 集成功能 |
| `queue_worker` | QueueManager 消费者、Expiry consumer、TTL maintenance |
| `scheduler` | 已启用的 Watch、Compile 补偿、Session 文件 idle、归档规则清理扫描；托管 Expiry publisher |

没有 `api` 时不创建 FastAPI 应用、不监听 HTTP 端口。`--workers` 表示该组合的进程数；所有子进程加载相同角色配置。HTTP 多进程由 Uvicorn 托管，后台多进程使用 spawn。后台任一子进程意外退出会结束整个部署并回收其余子进程，便于外部部署系统重启。`--with-bot` 需要 `api`。

共享资源先初始化，再安装账户能力、删除保护、队列 handler 和 TTL 写后 hook，最后启动执行角色。需要 Redis 到期登记的每个进程均保留 report 和完整 handler 登记选项；关闭 consumer 不会关闭 TTL 登记重试、Compile reschedule 等生产能力。配置和账户缓存刷新、Task 历史维护与本地取消轮询跟随进程依赖运行。

系统资产在账户能力就绪后持久化文件并登记处理任务，随后安装删除保护，再启动消费者。服务器启动不等待这些任务的终态，处理可由当前部署或独立 worker 完成。SDK 默认仍在 worker 启动后同步等待资产处理。

默认独立 Expiry publisher 仅由含 `scheduler` 的部署启动，在相应 Redis 功能开启时生效。父进程等待其 ready，监控意外退出并在结束时回收。显式配置 `publisher_separate_process=false` 时，由 scheduler 进程内运行；沿用 Expiry 分片协调。

后台进程收到 SIGINT/SIGTERM 后开始 stop。系统先停定时生产者和本地后台提交，再停 Expiry callback、排空队列写入，随后关闭 TTL 登记重试/maintenance、Task 维护循环和共享存储，最后关闭账户能力。若执行没有停止，stop 抛出错误并保留其依赖，可重试清理。进程强杀仍可能需要底层持久任务恢复。

`OpenVikingService.initialize()` 保持 SDK 默认启动全部后台职责；服务器通过 `initialize(start_background=false)` 准备共享图，再按角色启动。

角色组合不改变存储的部署条件：多个进程必须使用支持该部署方式的共享 Task、队列、账户和文件存储；本地存储独占锁仍保留。多个 scheduler 可启动，沿用各模块现有协调，本轮没有增加一个覆盖所有扫描的 leader。Watch 的进一步 Service/Runtime 拆分及多节点接管由对应模块实现交付；TTL 回填跨节点交错、Session/Rule 扫描的业务并发保护仍需专项验证。
