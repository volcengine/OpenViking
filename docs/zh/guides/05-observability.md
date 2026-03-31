# 可观测性与排障

这份指南把 OpenViking 当前和“观测”有关的入口放在一起介绍，包括：

- 服务健康检查与组件状态
- 请求级 `telemetry`
- 终端侧 `ov tui`
- Web 侧 `OpenViking Console`

如果你只想快速判断“该看哪里”，先看下面这张表。

## 先选哪个入口

| 入口 | 适合看什么 | 典型场景 |
| --- | --- | --- |
| `/health`、`observer/*` | 服务是否健康、队列是否堆积、VikingDB/VLM 状态 | 部署验收、值班巡检 |
| `ov tui` | `viking://` 文件树、目录摘要、文件正文、向量记录 | 开发调试、核对资源是否真正落库 |
| `OpenViking Console` | Web UI 里的文件浏览、检索、资源导入、租户与系统状态 | 不想手敲命令时做交互式排查 |
| `telemetry` | 单次请求耗时、token、向量检索、资源处理阶段 | 排查一次具体调用为什么慢、为什么结果异常 |

## 服务健康与组件状态

### 健康检查

`/health` 提供简单的存活检查，不需要认证。

```bash
curl http://localhost:1933/health
```

```json
{"status": "ok"}
```

### 整体系统状态

**Python SDK (Embedded / HTTP)**

```python
status = client.get_status()
print(f"Healthy: {status['is_healthy']}")
print(f"Errors: {status['errors']}")
```

**HTTP API**

```bash
curl http://localhost:1933/api/v1/observer/system \
  -H "X-API-Key: your-key"
```

```json
{
  "status": "ok",
  "result": {
    "is_healthy": true,
    "errors": [],
    "components": {
      "queue": {"name": "queue", "is_healthy": true, "has_errors": false},
      "vikingdb": {"name": "vikingdb", "is_healthy": true, "has_errors": false},
      "vlm": {"name": "vlm", "is_healthy": true, "has_errors": false}
    }
  }
}
```

### 组件状态

| 端点 | 组件 | 描述 |
| --- | --- | --- |
| `GET /api/v1/observer/queue` | Queue | 处理队列状态 |
| `GET /api/v1/observer/vikingdb` | VikingDB | 向量数据库状态 |
| `GET /api/v1/observer/vlm` | VLM | 视觉语言模型状态 |

例如：

```bash
curl http://localhost:1933/api/v1/observer/queue \
  -H "X-API-Key: your-key"
```

### 快速健康检查

**Python SDK (Embedded / HTTP)**

```python
if client.is_healthy():
    print("System OK")
```

**HTTP API**

```bash
curl http://localhost:1933/api/v1/debug/health \
  -H "X-API-Key: your-key"
```

```json
{"status": "ok", "result": {"healthy": true}}
```

### 响应时间

每个 API 响应都包含一个 `X-Process-Time` 请求头，表示服务端处理时间（单位为秒）：

```bash
curl -v http://localhost:1933/api/v1/fs/ls?uri=viking:// \
  -H "X-API-Key: your-key" 2>&1 | grep X-Process-Time
# < X-Process-Time: 0.0023
```

这部分解决的是“服务现在是不是活着、是不是堵了、哪个组件有问题”。如果你要看某一次请求内部发生了什么，请继续看 telemetry。

## 用 `ov tui` 看数据面

`ov` CLI 里有一个独立的 TUI 文件浏览器命令：

```bash
ov tui /
```

也可以从某个 scope 直接进入：

```bash
ov tui viking://resources
```

使用前提：

- OpenViking Server 已启动
- 已配置好 `ovcli.conf`
- 当前 `X-API-Key` 有权读取对应租户数据

这个 TUI 适合做两类观测：

- 看 `viking://resources`、`viking://user`、`viking://agent`、`viking://session` 下实际落了哪些数据
- 看某个 URI 对应的向量记录是否已经写入，以及数量是否符合预期

常用按键：

- `q`：退出
- `Tab`：在左侧树和右侧内容面板之间切换焦点
- `j` / `k`：上下移动
- `.`：展开或折叠目录
- `g` / `G`：跳到顶部或底部
- `v`：切换到向量记录视图
- `n`：在向量记录视图里加载下一页
- `c`：在向量记录视图里统计当前 URI 的向量总数

一个常见排查流程是：

1. 用 `ov tui viking://resources` 找到目标文档或目录。
2. 确认右侧能看到 `abstract` / `overview` / 正文内容。
3. 按 `v` 进入向量记录视图，确认该 URI 下是否已经有向量数据。
4. 按 `c` 查看总量，必要时按 `n` 翻页继续核对。

TUI 更偏“数据面排查”。它适合回答“资源到底有没有进去”“向量到底有没有写进去”，但不直接展示单次请求的 token 或阶段耗时。

## 用 OpenViking Console 做 Web 观测

仓库里还有一个独立的 Web Console，它不是主 CLI 的一部分，需要单独启动：

```bash
python -m openviking.console.bootstrap \
  --host 127.0.0.1 \
  --port 8020 \
  --openviking-url http://127.0.0.1:1933
```

然后打开：

```text
http://127.0.0.1:8020/
```

第一次使用时，在 `Settings` 面板里填入 `X-API-Key`。

当前比较适合观测的面板有：

- `FileSystem`：浏览 URI、查看目录和文件
- `Find`：直接发检索请求并查看结果
- `Add Resource`：导入资源并查看返回结果
- `Add Memory`：通过 session 提交一段内容，观察 memory 提交流程
- `Tenants` / `Monitor`：查看租户、用户以及系统状态

如果你要执行写操作，例如 `Add Resource`、`Add Memory`、租户或用户管理，需要带 `--write-enabled` 启动：

```bash
python -m openviking.console.bootstrap \
  --host 127.0.0.1 \
  --port 8020 \
  --openviking-url http://127.0.0.1:1933 \
  --write-enabled
```

从观测角度看，Console 的一个优点是结果面板会直接显示接口返回值。对于 `find`、`add-resource` 和 `session commit` 这类操作，Console 代理层会默认帮你请求 `telemetry`，所以页面结果里通常可以直接看到 `telemetry.summary`。

Console 更适合“边点边看”的交互式排查；如果你要把观测数据接到自己的日志系统或自动化链路，建议直接调用 HTTP API 或 SDK，并显式请求 telemetry。

## 请求级 Telemetry

OpenViking 的请求级追踪能力对外名称是 `operation telemetry`。它会在响应里附带一份结构化摘要，用来说明这次调用里发生了什么，例如：

- 总耗时
- LLM / embedding token 消耗
- 向量检索次数、扫描量、返回量
- 资源导入阶段耗时
- `session.commit` 的 memory 提取统计

最常见的请求方式是在 body 里显式传：

```json
{"telemetry": true}
```

例如：

```bash
curl -X POST http://localhost:1933/api/v1/search/find \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "query": "memory dedup",
    "limit": 5,
    "telemetry": true
  }'
```

完整字段、支持范围和更多示例见：

- [操作级 Telemetry 参考](07-operation-telemetry.md)

## 相关文档

- [部署](03-deployment.md) - 服务器设置
- [认证](04-authentication.md) - API Key 设置
- [操作级 Telemetry 参考](07-operation-telemetry.md) - 请求级结构化追踪
- [系统 API](../api/07-system.md) - 系统与 observer 接口参考
