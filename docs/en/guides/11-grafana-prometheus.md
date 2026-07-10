# Using Prometheus and Grafana to View OpenViking Metrics

This document provides a complete end-to-end guide from scratch:

1. Start OpenViking and verify that `/metrics` is accessible
2. Start Prometheus to scrape OpenViking metrics
3. Start Grafana and connect the Prometheus data source
4. Import the OpenViking built-in dashboard or query directly in Explore

If you can already access `http://<host>:<port>/metrics`, you can skip ahead to the "Start Prometheus" section of this document.

## Architecture Overview

OpenViking does not directly provide a Grafana page. The standard pipeline is:

```text
OpenViking -> /metrics -> Prometheus -> Grafana
```

Where:

- OpenViking is responsible for exposing Prometheus exposition text
- Prometheus is responsible for periodically scraping `/metrics`
- Grafana is responsible for reading from Prometheus and displaying dashboards

## Prerequisites

Before starting, please confirm:

- OpenViking Server is installed and can start normally
- Docker is installed and can be used to quickly start Prometheus and Grafana
- You know the HTTP address that OpenViking is currently listening on, for example `http://localhost:30300`

## Step 1: Confirm OpenViking Exposes `/metrics`

OpenViking must have metrics enabled first. Minimal configuration reference:

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

After writing the configuration to `~/.openviking/ov.conf`, restart OpenViking Server.

If you have not started the service yet, you can refer to:

```bash
openviking-server doctor
openviking-server --port 30300
```

Then verify:

```bash
curl http://localhost:30300/metrics
```

If the response includes text with the `openviking_` prefix, metrics are enabled. For example:

```text
# HELP openviking_http_requests_total Total number of HTTP requests
# TYPE openviking_http_requests_total counter
openviking_http_requests_total{method="GET",route="/api/v1/system/status",status="200"} 12
```

If the response returns `Prometheus metrics are disabled.`, the configuration has not taken effect or the service has not been restarted.

## Step 2: Deploy Using the Repository's Built-in Compose Files

The repository already provides a set of ready-to-run observability examples, located at:

- `examples/grafana/docker-compose.yml`
- `examples/grafana/prometheus.yml`
- `examples/grafana/grafana/provisioning/datasources/prometheus.yml`
- `examples/grafana/grafana/provisioning/dashboards/openviking.yml`

In addition, for the scenario where OpenViking continues to listen on `127.0.0.1` / `localhost` on Linux, the repository also provides a localhost-specific set of examples:

- `examples/grafana/docker-compose.localhost.yml`
- `examples/grafana/prometheus.localhost.yml`
- `examples/grafana/grafana/provisioning-localhost/datasources/prometheus.yml`
- `examples/grafana/grafana/provisioning-localhost/dashboards/openviking.yml`

The difference between the two approaches is:

- `docker-compose.yml`: the general-purpose approach, where Prometheus accesses the host from the container network, suitable when OpenViking listens on `0.0.0.0`
- `docker-compose.localhost.yml`: the Linux localhost approach, where Prometheus and Grafana directly use the host network, suitable when OpenViking continues to listen on `127.0.0.1`

If you do not currently want to expose OpenViking on `0.0.0.0`, it is recommended to use `docker-compose.localhost.yml` first.

By default, this configuration does several things:

- Starts Prometheus and maps the host port to `30909`
- Starts Grafana and maps the host port to `13000`
- Automatically configures the Grafana data source to `http://127.0.0.1:30909`
- Automatically loads the OpenViking demo dashboard from the repository
- Automatically loads `OpenViking - Feedback Baseline`, making it easy to directly view the baseline metrics for `openviking_feedback_*` and `openviking_feedback_channel_*`

### Approach A: General-Purpose

Run directly:

```bash
docker compose -f examples/grafana/docker-compose.yml up -d
```

After startup completes, you can access:

```text
Prometheus: http://localhost:30909
Grafana:    http://localhost:13000
```

In this example, the default Grafana credentials are fixed as:

- Username: `admin`
- Password: `admin`

### Approach B: Linux localhost

If your OpenViking continues to listen on `127.0.0.1:30300`, and you do not want to change OpenViking to `0.0.0.0` just for Prometheus scraping, use the following compose setup:

```bash
docker compose -f examples/grafana/docker-compose.localhost.yml up -d
```

The characteristics of this approach are:

- Prometheus uses the host network and directly scrapes `127.0.0.1:30300/metrics`
- Grafana also uses the host network and directly connects to `http://127.0.0.1:30909`
- There is no need to change OpenViking to `0.0.0.0`
- It does not trigger the security restriction that "non-localhost listening must configure `root_api_key`"

The access addresses are still:

```text
Prometheus: http://localhost:30909
Grafana:    http://localhost:13000
```

If `30909` or `13000` on the host is already occupied:

- For the Prometheus port, change `--web.listen-address=0.0.0.0:30909` in `examples/grafana/docker-compose.localhost.yml`
- For the Grafana port, change `GF_SERVER_HTTP_PORT=13000` in `examples/grafana/docker-compose.localhost.yml`
- At the same time, change `http://127.0.0.1:30909` in `examples/grafana/grafana/provisioning-localhost/datasources/prometheus.yml` to the new port

If you only want a quick deployment, once you reach this point you can jump ahead to "How to Verify the Entire Pipeline Is Working".

## Step 3: Understanding the Prometheus Scrape Configuration

The contents of `examples/grafana/prometheus.yml` used in the compose example are as follows:

```yaml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: openviking
    metrics_path: /metrics
    static_configs:
      - targets: ["host.docker.internal:30300"]
```

Explanation:

- If Prometheus runs inside a Docker container while OpenViking runs on the host, it is recommended to write `targets` as `host.docker.internal:30300`
- If Prometheus also runs on the host, change it to `localhost:30300`
- If `host.docker.internal` is not available in your Linux Docker environment, change it to the actual host IP, for example `192.168.1.10:30300`

If your OpenViking is not listening on `30300`, change the target address in this file to your actual port, then re-run:

```bash
docker compose -f examples/grafana/docker-compose.yml up -d
```

If you are using the Linux localhost approach, the corresponding file to modify is:

- `examples/grafana/prometheus.localhost.yml`

For example, if OpenViking is actually listening on `127.0.0.1:1933`, change it to:

```yaml
targets: ["127.0.0.1:1933"]
```

Then re-run:

```bash
docker compose -f examples/grafana/docker-compose.localhost.yml up -d
```

## Step 4: Optional - Create a Docker Network for Manual Deployment

If you are using the compose files above, you do not need to perform this step manually, because Compose automatically creates the default network.

Only when you insist on using `docker run` to start Prometheus and Grafana separately do you need to create an independent network first:

```bash
docker network create openviking-observability
```

If it reports that the network already exists, you can ignore it.

## Step 5: Optional - Manually Start Prometheus

If you have already used `docker compose -f examples/grafana/docker-compose.yml up -d`, you can skip this section.

On many machines, `9090` is already occupied by another service. To reduce conflicts, it is recommended here to map the host port to `30909`:

```bash
docker run -d \
  --name prometheus \
  --network openviking-observability \
  -p 30909:9090 \
  -v "$PWD/prometheus.yml:/etc/prometheus/prometheus.yml:ro" \
  prom/prometheus
```

After startup, open in your browser:

```text
http://localhost:30909
```

Once in the Prometheus UI, enter the following in the query box:

```promql
openviking_http_requests_total
```

Or:

```promql
openviking_service_readiness
```

If you can find time series, it means Prometheus has successfully scraped OpenViking metrics.

### If the Prometheus Container Fails to Start

Common cause: the host port is occupied, for example:

```text
Bind for 0.0.0.0:9090 failed: port is already allocated
```

How to handle:

- Change the host port, for example continue using `30909:9090`
- Do not change the container-internal port `9090`
- Use the new host port when accessing, for example `http://localhost:30909`

## Step 6: Optional - Manually Start Grafana

If you have already used `docker compose -f examples/grafana/docker-compose.yml up -d`, you can skip this section.

Similarly, `3000` on many machines is also often occupied. It is recommended to map Grafana to host port `13000`:

```bash
docker run -d \
  --name grafana \
  --network openviking-observability \
  -p 13000:3000 \
  grafana/grafana
```

After startup, open:

```text
http://localhost:13000
```

The default initial Grafana credentials are usually:

- Username: `admin`
- Password: `admin`

If your environment has changed the default credentials, use the actual values.

## Step 7: Optional - Manually Add the Prometheus Data Source in Grafana

If you are using the repository's built-in compose files, you can usually skip this step as well, because the data source is automatically provisioned.

In the Grafana UI:

1. Open `Connections` or `Data sources` in the left sidebar
2. Click `Add data source`
3. Select `Prometheus`
4. In the `URL` field, enter: `http://prometheus:9090`
5. Click `Save & test`

The reason for entering `http://prometheus:9090` here is:

- Grafana and Prometheus run in the same Docker network `openviking-observability`
- The two containers can communicate directly via the container name

If `Save & test` fails, first run:

```bash
docker ps
```

Confirm that both the `prometheus` and `grafana` containers are running.

## Step 8: First Query Directly in Grafana Explore

After adding the data source, do not rush to import a dashboard. It is recommended to first verify basic queries in `Explore`.

It is recommended to try these queries first:

Request volume:

```promql
rate(openviking_http_requests_total[5m])
```

View request volume and status codes by route:

```promql
sum by (route, status) (rate(openviking_http_requests_total[5m]))
```

P95 latency:

```promql
histogram_quantile(0.95, sum by (le, route) (rate(openviking_http_request_duration_seconds_bucket[5m])))
```

Queue backlog:

```promql
openviking_queue_pending
```

Model call volume:

```promql
rate(openviking_model_calls_total[5m])
```

Token usage:

```promql
rate(openviking_operation_tokens_total[5m])
```

If you are not yet sure which metric names exist, you can first query:

```promql
{__name__=~"openviking_.*"}
```

## Step 9: Import the OpenViking Built-in Dashboards

If you are using the repository's built-in compose files, these two dashboards will be automatically loaded into the `OpenViking` folder after Grafana starts.

If you want to import them manually, just follow the steps below.

The repository already provides Grafana dashboards that can be imported directly:

- `examples/grafana/openviking_demo_dashboard.json`
- `examples/grafana/openviking_token_demo_dashboard.json`

Import steps:

1. Go to `Dashboards` in the left sidebar of Grafana
2. Click `New` or `Import` in the top right corner
3. Upload `examples/grafana/openviking_demo_dashboard.json`
4. On the import page, select the Prometheus data source you just created
5. Click `Import`

Notes:

- `openviking_demo_dashboard.json` is suitable as a basic overview dashboard
- `openviking_token_demo_dashboard.json` depends on the `tim012432-calendarheatmap-panel` plugin; before it is installed, some panels may not display properly

## Step 10: How to Verify the Entire Pipeline Is Working

You can verify in the following order:

1. `curl http://localhost:30300/metrics` returns metric text
2. Open `http://localhost:30909` and you can find `openviking_http_requests_total` in Prometheus
3. Open `http://localhost:13000` and see that the `Prometheus` data source already exists, or that a manual `Save & test` succeeds
4. Running `rate(openviking_http_requests_total[5m])` in Grafana Explore produces a graph
5. After importing the demo dashboard, the panels begin to display data

As long as all five steps pass, the entire pipeline is working.

## FAQ

### 1. `/metrics` is accessible, but Prometheus cannot find any data

First check:

- Whether `targets` in `prometheus.yml` is written correctly
- Whether Prometheus has actually reloaded the new configuration
- Whether the Docker container can access port `30300` on the host

If you are using the repository's built-in compose files, first check:

```bash
docker compose -f examples/grafana/docker-compose.yml logs prometheus
```

If you suspect a container-to-host access issue, you can change `host.docker.internal` to the actual host IP.

### 2. The Prometheus Host Port Is Occupied

Example error:

```text
Bind for 0.0.0.0:9090 failed: port is already allocated
```

How to handle: change to a different host port, for example:

```bash
  -p 30909:9090
```

### 3. The Grafana Host Port Is Occupied

How to handle: change to a different host port, for example:

```bash
-p 13000:3000
```

### 4. There Are No OpenViking Metrics in Grafana

First check:

- Whether the Grafana data source is actually connected to Prometheus
- Whether `openviking_*` metrics already exist in Prometheus
- Whether the time range is too short, resulting in no recent samples

If you are using the compose auto-import approach, you can also first confirm whether the dashboard has been loaded:

- Go to `Dashboards` in the left sidebar
- Check whether the `OpenViking` folder exists

### 5. The Dashboard Imports Successfully but the Panels Are Empty

This is usually not because the dashboard file is corrupted, but because:

- The corresponding metric samples do not yet exist in Prometheus
- The filter conditions do not match the current environment
- The wrong data source was selected

It is recommended to first go back to Explore and manually run PromQL to confirm that the basic queries do have data.

## Related Documents

- [Observability and Troubleshooting](05-observability.md)
- [Validating Vikingbot Metrics with Real Q&A](12-vikingbot-metrics-validation.md)
- [Metrics](../concepts/12-metrics.md)
- [Metrics API](../api/09-metrics.md)
- [Server Deployment](03-deployment.md)
- [Quick Start: Server Mode](../getting-started/03-quickstart-server.md)
