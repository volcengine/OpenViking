# 可观测性与排障

这份指南把 OpenViking 当前和“观测”有关的入口放在一起介绍，包括：

- 服务健康检查与组件状态
- 请求级 `telemetry`
- 终端侧 `ov tui`
- Web 侧 `OpenViking Console`
- `/metrics` 时序指标

如果你只想快速判断“该看哪里”，先看下面这张表。

## 先选哪个入口

| 入口 | 适合看什么 | 典型场景 |
| --- | --- | --- |
| `/health`、`observer/*` | 服务是否健康、队列是否堆积、VikingDB/VLM 状态 | 部署验收、值班巡检 |
| `ov tui` | `viking://` 文件树、目录摘要、文件正文、向量记录、受支持图片文件的预览 | 开发调试、核对资源是否真正落库 |
| `OpenViking Console` | Web UI 里的文件浏览、检索、资源导入、租户与系统状态 | 不想手敲命令时做交互式排查 |
| `telemetry` | 单次请求耗时、token、向量检索、资源处理阶段 | 排查一次具体调用为什么慢、为什么结果异常 |
| `/metrics` | 请求量趋势、错误率、时延分布、队列与探针状态 | Prometheus 抓取、Grafana 看板、告警规则 |

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
2. 确认右侧能看到 `abstract` / `overview` / 正文内容（受支持的图片文件 —— `png` / `jpg` / `jpeg` / `gif` / `bmp` / `webp` / `tiff` / `tif` —— 会直接渲染预览）。
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

## 用 `/metrics` 做时序观测

`/metrics` 是 OpenViking 面向 Prometheus 抓取模型提供的时序指标端点，适合回答这类问题：

- 最近一段时间 HTTP 请求量是不是突然升高了
- 某个接口或操作的错误率是不是在持续上升
- 请求耗时分布是否变差
- 队列是否开始堆积
- 关键依赖、探针或模型提供方是否进入不健康状态

和前面的 `observer/*` 相比，`/metrics` 更适合看**趋势、聚合和告警**；而 `observer/*` 更适合人工查看某一时刻的瞬时状态。

和前面的 `telemetry` 相比，`/metrics` 关注的是**聚合后的时间序列**；`telemetry` 关注的是**某一次请求内部到底发生了什么**。

### 快速开启 metrics

`/metrics` 默认是关闭的：当指标体系未启用时，访问会返回 `404`，并提示 `Prometheus metrics are disabled.`。

开启方式不需要完整配置，只需要在 `ov.conf` 的 `server` 段打开总开关即可。

**最小配置（推荐）**

在 `~/.openviking/ov.conf`（或你启动时通过 `--config` 指定的路径）里加入：

```json
{
  "server": {
    "observability": {
      "metrics": {
        "enabled": true
      }
    }
  }
}
```
改完配置后需要**重启 OpenViking Server** 才会生效。

### observability 配置层级

OpenViking 将信号级别的可观测性配置统一放在 `server.observability` 下：

- `server.observability.metrics`：metrics 子系统与 exporter 配置
- `server.observability.traces`：trace 导出配置
- `server.observability.logs`：log 导出配置
- `server.observability.dump_body`：把 HTTP 请求/响应 body（按 content-type 过滤、按字节截断）作为属性挂到当前 trace span 上，便于在 trace UI 中调试。默认关闭，因为 body 可能含密钥/高基数内容

示例：

```json
{
  "server": {
    "observability": {
      "metrics": {
        "enabled": true,
        "exporters": {
          "prometheus": {
            "enabled": true
          },
          "otel": {
            "enabled": true,
            "protocol": "grpc",
            "tls": {
              "insecure": true
            },
            "endpoint": "otel-collector:4317",
            "service_name": "openviking-server",
            "export_interval_ms": 10000,
            "headers": {}
          }
        }
      },
      "traces": {
        "enabled": true,
        "protocol": "grpc",
        "tls": {
          "insecure": true
        },
        "endpoint": "otel-collector:4317",
        "service_name": "openviking-server",
        "headers": {}
      },
      "logs": {
        "enabled": true,
        "protocol": "grpc",
        "tls": {
          "insecure": true
        },
        "endpoint": "otel-collector:4317",
        "service_name": "openviking-server",
        "headers": {}
      },
      "dump_body": {
        "enabled": false,
        "max_bytes": 4096
      }
    }
  }
}
```

说明：

- `headers` 用于给 OTLP exporter 透传自定义请求头或 gRPC metadata。
- 常见场景包括直连需要额外鉴权头的 OTLP 后端；请只配置 header key/value，不要把敏感值写入日志或截图中。
- 对 `traces`、`logs` 和 `metrics.exporters.otel` 三条链路，`headers` 的配置方式保持一致。
- 当 `protocol="grpc"` 时，`headers` 会作为 gRPC metadata 发送，key 需要使用小写形式，例如 `x-byteapm-appkey`；该限制不适用于 `protocol="http"`。

完整字段、支持范围和更多示例见：

- [指标](../concepts/12-metrics.md) 

### 直接访问 `/metrics`

当前实现中，`/metrics` 未接入 `get_request_context` 等鉴权依赖，因此从代码行为上看，它当前等价于公开抓取端点：

```bash
curl http://localhost:1933/metrics
```

如果你的部署环境通过网关、反向代理或服务发现层对 `/metrics` 做了保护，则应按部署方式附加鉴权。

### Prometheus 抓取示例

最常见的使用方式是让 Prometheus 定时抓取：

```yaml
scrape_configs:
  - job_name: openviking
    metrics_path: /metrics
    static_configs:
      - targets: ["localhost:1933"]
```

### 在 Grafana 中导入和查看 Dashboard

如果你已经让 Prometheus 成功抓取 `/metrics`，下一步最常见的做法就是在 Grafana 中导入 OpenViking 的 demo dashboard。

**第 1 步：先确认 Prometheus 已经抓到 `/metrics`**

在导入 Grafana dashboard 之前，先确认 Prometheus 数据源里已经能查到 OpenViking 指标。最简单的判断方式是：

- 在 Prometheus UI 里执行 `openviking_http_requests_total`
- 或执行 `openviking_service_readiness`
- 如果已经能返回时间序列，说明 Grafana 后续就能正常出图

如果这一步没有数据，先回到上面的 Prometheus 抓取配置，确认 `targets`、`metrics_path` 和网络连通性。

**第 2 步：在 Grafana 导入官方 demo dashboard**

OpenViking 仓库里已经提供了可直接导入的 dashboard JSON：

- [openviking_demo_dashboard.json](../../../examples/grafana/openviking_demo_dashboard.json)
- [openviking_token_demo_dashboard.json](../../../examples/grafana/openviking_token_demo_dashboard.json) （注意，该 dashboard 依赖 `tim012432-calendarheatmap-panel` grafana 插件，需要先安装才能正常工作）

导入步骤可以按下面做：

1. 登录你的 Grafana。
2. 在左侧菜单进入 `Dashboards`。
3. 点击右上角的 `New` 或 `Import`。
4. 选择上传 JSON 文件，或把上面链接对应文件的内容粘贴进去。
5. 在导入页面选择 Prometheus 作为数据源。
6. 点击 `Import` 完成导入。

如果导入后面板为空，通常优先检查两件事：

- Grafana 绑定的数据源是不是正确的 Prometheus
- Prometheus 里是否真的已经抓到了 `openviking_*` 指标

**第 3 步：打开 dashboard 后重点看什么**

接入之后，通常就可以在 Grafana 里重点观察这些指标族对应的面板：

- `openviking_http_*`：HTTP 请求量、耗时、inflight
- `openviking_operation_*`：结构化操作的成功率和耗时
- `openviking_queue_*`：队列处理量、积压和执行中数量
- `openviking_*_readiness`：依赖与探针健康状态

**第 4 步：最终效果长什么样**

导入成功后，你最终会看到一个以 OpenViking 请求、队列、探针、模型调用和系统状态为主的总览 dashboard。效果示意可以参考：

- [grafana-demo-dashboard.png](../../images/grafana-demo-dashboard.png)

这张图可以帮助你快速确认“导入后的面板布局是不是正常”。如果你的 dashboard 基本结构和它一致，但局部面板没有数据，通常说明是对应指标当前没有产生样本，或者筛选条件与实际流量不匹配。

### 如何理解常见标签

排查看板时，最常见的几个标签是：

- `account_id`：租户维度标签。只在受控白名单指标上开启，未识别请求会被归到 `__unknown__`，超出活跃租户预算时会落到 `__overflow__`
- `route`：HTTP 路由模板，例如 `/api/v1/search/find`
- `status`：请求或阶段状态，例如 `200`、`ok`、`error`
- `valid`：当前样本是否是本次成功刷新得到的有效值；`valid="0"` 通常表示失败回退值或 stale fallback

### 什么时候看 `/metrics`，什么时候看别的入口

- 看服务是否整体健康、哪个组件当前不通：先看 `/health` 和 `observer/*`
- 看资源是否真的落库、向量是否真的写进去：看 `ov tui`
- 看某一次具体请求为什么慢、token 花在哪、资源处理卡在哪个阶段：看 `telemetry`
- 看一段时间内请求量、错误率、时延是否持续恶化：看 `/metrics`

## 相关文档

- [使用 Prometheus 和 Grafana 查看 OpenViking 指标](11-grafana-prometheus.md) - 从 `/metrics` 到 Prometheus、Grafana dashboard 的完整操作流程
- [使用真实问答验证 Vikingbot 指标](12-vikingbot-metrics-validation.md) - 用 `/bot/v1/chat`、`/bot/v1/feedback` 和真实 follow-up 场景校验反馈与 outcome 指标
- [部署](03-deployment.md) - 服务器设置
- [认证](04-authentication.md) - API Key 设置
- [操作级 Telemetry 参考](07-operation-telemetry.md) - 请求级结构化追踪
- [系统 API](../api/07-system.md) - 系统与 observer 接口参考
- [指标](../concepts/12-metrics.md) - 时序指标与配置
