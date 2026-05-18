# 使用 Prometheus 和 Grafana 查看 OpenViking 指标

这份文档给出一条从零开始的完整链路：

1. 启动 OpenViking 并确认 `/metrics` 可访问
2. 启动 Prometheus 抓取 OpenViking 指标
3. 启动 Grafana 并连接 Prometheus 数据源
4. 导入 OpenViking 自带 dashboard 或在 Explore 中直接查询

如果你已经能访问 `http://<host>:<port>/metrics`，可以直接从本文的“启动 Prometheus”开始。

## 架构关系

OpenViking 不直接提供 Grafana 页面。标准链路是：

```text
OpenViking -> /metrics -> Prometheus -> Grafana
```

其中：

- OpenViking 负责暴露 Prometheus exposition 文本
- Prometheus 负责定时抓取 `/metrics`
- Grafana 负责读取 Prometheus 并展示 dashboard

## 前置条件

开始前请确认：

- OpenViking Server 已安装并可正常启动
- Docker 已安装，可用于快速启动 Prometheus 和 Grafana
- 你知道 OpenViking 当前监听的 HTTP 地址，例如 `http://localhost:30300`

## 第 1 步：确认 OpenViking 已暴露 `/metrics`

OpenViking 需要先启用 metrics。最小配置参考：

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

配置写入 `~/.openviking/ov.conf` 后，重启 OpenViking Server。

如果你还没有启动服务，可参考：

```bash
openviking-server doctor
openviking-server --port 30300
```

然后验证：

```bash
curl http://localhost:30300/metrics
```

如果返回包含 `openviking_` 前缀的文本，说明 metrics 已经启用。例如：

```text
# HELP openviking_http_requests_total Total number of HTTP requests
# TYPE openviking_http_requests_total counter
openviking_http_requests_total{method="GET",route="/api/v1/system/status",status="200"} 12
```

如果返回 `Prometheus metrics are disabled.`，说明配置未生效或服务未重启。

## 第 2 步：使用仓库自带 compose 文件部署

仓库里已经提供了一套可直接启动的观测示例，文件位于：

- `examples/grafana/docker-compose.yml`
- `examples/grafana/prometheus.yml`
- `examples/grafana/grafana/provisioning/datasources/prometheus.yml`
- `examples/grafana/grafana/provisioning/dashboards/openviking.yml`

另外，针对 Linux 上 OpenViking 继续监听 `127.0.0.1` / `localhost` 的场景，仓库还提供了一套 localhost 专用示例：

- `examples/grafana/docker-compose.localhost.yml`
- `examples/grafana/prometheus.localhost.yml`
- `examples/grafana/grafana/provisioning-localhost/datasources/prometheus.yml`
- `examples/grafana/grafana/provisioning-localhost/dashboards/openviking.yml`

两套方案的区别是：

- `docker-compose.yml`：通用方案，Prometheus 从容器网络访问宿主机，适合 OpenViking 监听 `0.0.0.0`
- `docker-compose.localhost.yml`：Linux localhost 方案，Prometheus 和 Grafana 直接使用宿主机网络，适合 OpenViking 继续监听 `127.0.0.1`

如果你当前不想把 OpenViking 暴露到 `0.0.0.0`，推荐优先使用 `docker-compose.localhost.yml`。

这套配置默认会做几件事：

- 启动 Prometheus，并把宿主机端口映射到 `30909`
- 启动 Grafana，并把宿主机端口映射到 `13000`
- 自动把 Grafana 数据源配置为 `http://127.0.0.1:30909`
- 自动加载仓库里的 OpenViking demo dashboard
- 自动加载 `OpenViking - Feedback Baseline`，方便直接查看 `openviking_feedback_*` 与 `openviking_feedback_channel_*` 的基线指标

### 方案 A：通用方案

直接执行：

```bash
docker compose -f examples/grafana/docker-compose.yml up -d
```

启动完成后可访问：

```text
Prometheus: http://localhost:30909
Grafana:    http://localhost:13000
```

Grafana 默认账号密码在这个示例里固定为：

- 用户名：`admin`
- 密码：`admin`

### 方案 B：Linux localhost 方案

如果你的 OpenViking 继续监听在 `127.0.0.1:30300`，并且你不想为了 Prometheus 抓取而把 OpenViking 改成 `0.0.0.0`，请使用下面这套 compose：

```bash
docker compose -f examples/grafana/docker-compose.localhost.yml up -d
```

这套方案的特点是：

- Prometheus 使用宿主机网络，直接抓取 `127.0.0.1:30300/metrics`
- Grafana 也使用宿主机网络，并直接连接 `http://127.0.0.1:30909`
- 不需要把 OpenViking 改成 `0.0.0.0`
- 不会触发“非 localhost 监听必须配置 `root_api_key`”这条安全限制

访问地址仍然是：

```text
Prometheus: http://localhost:30909
Grafana:    http://localhost:13000
```

如果宿主机上的 `30909` 或 `13000` 已经被占用：

- Prometheus 端口改 `examples/grafana/docker-compose.localhost.yml` 里的 `--web.listen-address=0.0.0.0:30909`
- Grafana 端口改 `examples/grafana/docker-compose.localhost.yml` 里的 `GF_SERVER_HTTP_PORT=13000`
- 同时把 `examples/grafana/grafana/provisioning-localhost/datasources/prometheus.yml` 中的 `http://127.0.0.1:30909` 改成新端口

如果你只想快速部署，做到这里就可以先跳到“如何判断链路已经完全打通”。

## 第 3 步：理解 Prometheus 抓取配置

compose 示例里使用的 `examples/grafana/prometheus.yml` 内容如下：

```yaml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: openviking
    metrics_path: /metrics
    static_configs:
      - targets: ["host.docker.internal:30300"]
```

说明：

- 如果 Prometheus 运行在 Docker 容器里，而 OpenViking 运行在宿主机，`targets` 推荐写成 `host.docker.internal:30300`
- 如果 Prometheus 也运行在宿主机，改成 `localhost:30300`
- 如果 `host.docker.internal` 在你的 Linux Docker 环境中不可用，就改成宿主机实际 IP，例如 `192.168.1.10:30300`

如果你的 OpenViking 不是监听在 `30300`，就把这个文件里的目标地址改成你的实际端口，然后重新执行：

```bash
docker compose -f examples/grafana/docker-compose.yml up -d
```

如果你使用的是 Linux localhost 方案，对应修改的是：

- `examples/grafana/prometheus.localhost.yml`

例如 OpenViking 实际监听 `127.0.0.1:1933`，就改成：

```yaml
targets: ["127.0.0.1:1933"]
```

然后重新执行：

```bash
docker compose -f examples/grafana/docker-compose.localhost.yml up -d
```

## 第 4 步：可选，手动部署时创建 Docker 网络

如果你使用的是上面的 compose 文件，这一步不需要手动执行，因为 Compose 会自动创建默认网络。

只有在你坚持使用 `docker run` 分开启动 Prometheus 和 Grafana 时，才需要先创建一个独立网络：

```bash
docker network create openviking-observability
```

如果提示网络已存在，可以忽略。

## 第 5 步：可选，手动启动 Prometheus

如果你已经用了 `docker compose -f examples/grafana/docker-compose.yml up -d`，这一节可以跳过。

很多机器上 `9090` 已经被别的服务占用。为了减少冲突，这里建议把宿主机端口映射到 `30909`：

```bash
docker run -d \
  --name prometheus \
  --network openviking-observability \
  -p 30909:9090 \
  -v "$PWD/prometheus.yml:/etc/prometheus/prometheus.yml:ro" \
  prom/prometheus
```

启动后，在浏览器打开：

```text
http://localhost:30909
```

进入 Prometheus UI 后，在查询框中输入：

```promql
openviking_http_requests_total
```

或者：

```promql
openviking_service_readiness
```

如果能查到时间序列，说明 Prometheus 已经成功抓到 OpenViking 指标。

### 如果 Prometheus 容器启动失败

常见原因：宿主机端口被占用，例如：

```text
Bind for 0.0.0.0:9090 failed: port is already allocated
```

处理方式：

- 改宿主机端口，例如继续使用 `30909:9090`
- 不要改容器内端口 `9090`
- 访问时用新的宿主机端口，例如 `http://localhost:30909`

## 第 6 步：可选，手动启动 Grafana

如果你已经用了 `docker compose -f examples/grafana/docker-compose.yml up -d`，这一节可以跳过。

同样地，很多机器上的 `3000` 也常被占用。建议把 Grafana 映射到宿主机的 `13000`：

```bash
docker run -d \
  --name grafana \
  --network openviking-observability \
  -p 13000:3000 \
  grafana/grafana
```

启动后打开：

```text
http://localhost:13000
```

Grafana 默认初始账号通常是：

- 用户名：`admin`
- 密码：`admin`

如果你的环境已修改默认凭据，以实际值为准。

## 第 7 步：可选，手动在 Grafana 中添加 Prometheus 数据源

如果你使用的是仓库自带 compose 文件，这一步通常也可以跳过，因为数据源会自动 provision。

在 Grafana 页面中操作：

1. 打开左侧 `Connections` 或 `Data sources`
2. 点击 `Add data source`
3. 选择 `Prometheus`
4. 在 `URL` 中填写：`http://prometheus:9090`
5. 点击 `Save & test`

这里填写 `http://prometheus:9090` 的原因是：

- Grafana 和 Prometheus 运行在同一个 Docker 网络 `openviking-observability` 中
- 两个容器可以直接通过容器名通信

如果 `Save & test` 失败，请先执行：

```bash
docker ps
```

确认 `prometheus` 和 `grafana` 两个容器都在运行。

## 第 8 步：先在 Grafana Explore 中直接查询

添加完数据源后，先不要急着导入 dashboard，建议先在 `Explore` 中验证基础查询。

推荐先试这些查询：

请求量：

```promql
rate(openviking_http_requests_total[5m])
```

按路由查看请求量与状态码：

```promql
sum by (route, status) (rate(openviking_http_requests_total[5m]))
```

P95 延迟：

```promql
histogram_quantile(0.95, sum by (le, route) (rate(openviking_http_request_duration_seconds_bucket[5m])))
```

队列积压：

```promql
openviking_queue_pending
```

模型调用量：

```promql
rate(openviking_model_calls_total[5m])
```

Token 用量：

```promql
rate(openviking_operation_tokens_total[5m])
```

如果你还不确定有哪些指标名，可以先查：

```promql
{__name__=~"openviking_.*"}
```

## 第 9 步：导入 OpenViking 自带 Dashboard

如果你使用的是仓库自带 compose 文件，这两个 dashboard 会在 Grafana 启动后自动加载到 `OpenViking` 文件夹下。

如果你想手动导入，继续按下面步骤操作即可。

仓库中已经提供了可直接导入的 Grafana dashboard：

- `examples/grafana/openviking_demo_dashboard.json`
- `examples/grafana/openviking_token_demo_dashboard.json`

导入步骤：

1. 进入 Grafana 左侧 `Dashboards`
2. 点击右上角 `New` 或 `Import`
3. 上传 `examples/grafana/openviking_demo_dashboard.json`
4. 在导入页面选择刚刚创建的 Prometheus 数据源
5. 点击 `Import`

说明：

- `openviking_demo_dashboard.json` 适合作为基础总览 dashboard
- `openviking_token_demo_dashboard.json` 依赖 `tim012432-calendarheatmap-panel` 插件，未安装前部分面板可能无法正常显示

## 第 10 步：如何判断链路已经完全打通

你可以按下面的顺序验证：

1. `curl http://localhost:30300/metrics` 能返回指标文本
2. 打开 `http://localhost:30909`，在 Prometheus 中能查到 `openviking_http_requests_total`
3. 打开 `http://localhost:13000`，能看到 `Prometheus` 数据源已经存在，或手动 `Save & test` 成功
4. Grafana Explore 中运行 `rate(openviking_http_requests_total[5m])` 能出图
5. 导入 demo dashboard 后面板开始显示数据

只要这五步都通过，说明整条链路已经打通。

## 常见问题

### 1. `/metrics` 能访问，但 Prometheus 查不到数据

优先检查：

- `prometheus.yml` 的 `targets` 是否写对
- Prometheus 是否真的重新加载了新的配置
- Docker 容器内是否能访问宿主机上的 `30300`

如果你使用的是仓库自带 compose 文件，优先检查：

```bash
docker compose -f examples/grafana/docker-compose.yml logs prometheus
```

如果怀疑容器访问宿主机有问题，可以把 `host.docker.internal` 改成宿主机实际 IP。

### 2. Prometheus 宿主机端口被占用

报错示例：

```text
Bind for 0.0.0.0:9090 failed: port is already allocated
```

处理方式：改成别的宿主机端口，例如：

```bash
  -p 30909:9090
```

### 3. Grafana 宿主机端口被占用

处理方式：改成别的宿主机端口，例如：

```bash
-p 13000:3000
```

### 4. Grafana 里没有任何 OpenViking 指标

优先检查：

- Grafana 数据源是否真的连到 Prometheus
- Prometheus 中是否已经有 `openviking_*` 指标
- 时间范围是否过短，导致近期没有样本

如果你使用的是 compose 自动导入方案，还可以先确认 dashboard 是否已经被加载：

- 左侧进入 `Dashboards`
- 查看 `OpenViking` 文件夹是否存在

### 5. Dashboard 导入成功但面板为空

这通常不是 dashboard 文件损坏，而是：

- Prometheus 里还没有对应指标样本
- 过滤条件和当前环境不匹配
- 选择了错误的数据源

建议先回到 Explore 手动执行 PromQL，确认基础查询确实有数据。

## 相关文档

- [可观测性与排障](05-observability.md)
- [使用真实问答验证 Vikingbot 指标](12-vikingbot-metrics-validation.md)
- [指标与 Metrics](../concepts/12-metrics.md)
- [Metrics API](../api/09-metrics.md)
- [服务端部署](03-deployment.md)
- [快速开始：服务端模式](../getting-started/03-quickstart-server.md)
