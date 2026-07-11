# OpenViking Helm Chart

Deploy OpenViking on Kubernetes using Helm.

## Prerequisites

- Kubernetes 1.24+
- Helm 3.x
- A storage class that supports `ReadWriteOnce` persistent volumes (for RocksDB data)

## Installation

### Quick Start

```bash
helm install openviking ./deploy/helm/openviking \
  --set-string config.server.root_api_key="YOUR_ROOT_API_KEY" \
  --set-string config.embedding.dense.api_key="YOUR_VOLCENGINE_API_KEY" \
  --set-string config.vlm.api_key="YOUR_VOLCENGINE_API_KEY"
```

> **`root_api_key` is required by default.** When `config.server.host` is
> `0.0.0.0` (the default), the server refuses to start without a root API key.
> The chart now fails fast at `helm install/upgrade` time if it is missing,
> rather than leaving you to discover a `CrashLoopBackOff`. See
> [Using Secrets for API Keys](#using-secrets-for-api-keys) for the production
> pattern, or set `config.server.host: "127.0.0.1"` to run without one.`

The chart deploys `ghcr.io/volcengine/openviking:{appVersion}` by default (the
release this chart was tested with). To choose a different image tag:

```bash
# newest image from the main branch
helm upgrade --install openviking ./deploy/helm/openviking --set image.tag=main

# explicit latest (mutable, use Always pullPolicy)
helm upgrade --install openviking ./deploy/helm/openviking --set image.tag=latest

# pinned release image
helm upgrade --install openviking ./deploy/helm/openviking --set image.tag=v0.4.9
```

### Install with Custom Values

Create a `my-values.yaml` file:

```yaml
replicaCount: 1

resources:
  limits:
    cpu: "4"
    memory: 8Gi
  requests:
    cpu: "1"
    memory: 2Gi

persistence:
  size: 50Gi
  storageClass: "gp3"

config:
  storage:
    workspace: /app/.openviking/openviking_workspace
  log:
    level: INFO
    output: stdout
  server:
    host: "0.0.0.0"
    port: 1933
    workers: 1
    root_api_key: "your-secret-key"
  embedding:
    dense:
      api_base: "https://ark.cn-beijing.volces.com/api/v3"
      api_key: "your-volcengine-api-key"
      provider: "volcengine"
      dimension: 1024
      model: "doubao-embedding-vision-251215"
      input: "multimodal"
    max_concurrent: 10
  vlm:
    api_base: "https://ark.cn-beijing.volces.com/api/v3"
    api_key: "your-volcengine-api-key"
    provider: "volcengine"
    model: "doubao-seed-2-0-lite-260428"
    temperature: 0.0
    max_retries: 2
    thinking: false
    max_concurrent: 100
```

Then install:

```bash
helm install openviking ./deploy/helm/openviking -f my-values.yaml
```

### Using Secrets for API Keys

For production, avoid putting API keys directly in values. Use `extraEnv` with
Kubernetes secrets instead:

```bash
# Create a secret
kubectl create secret generic openviking-api-keys \
  --from-literal=root-api-key="YOUR_ROOT_API_KEY" \
  --from-literal=embedding-api-key="YOUR_KEY" \
  --from-literal=vlm-api-key="YOUR_KEY"
```

Then reference it in your values:

```yaml
config:
  server:
    root_api_key: "${OPENVIKING_ROOT_API_KEY}"
  embedding:
    dense:
      api_key: "${OPENVIKING_EMBEDDING_API_KEY}"
  vlm:
    api_key: "${OPENVIKING_VLM_API_KEY}"

extraEnv:
  - name: OPENVIKING_ROOT_API_KEY
    valueFrom:
      secretKeyRef:
        name: openviking-api-keys
        key: root-api-key
  - name: OPENVIKING_EMBEDDING_API_KEY
    valueFrom:
      secretKeyRef:
        name: openviking-api-keys
        key: embedding-api-key
  - name: OPENVIKING_VLM_API_KEY
    valueFrom:
      secretKeyRef:
        name: openviking-api-keys
        key: vlm-api-key
```

OpenViking expands environment variables inside `ov.conf` at startup, so the
ConfigMap can contain placeholders while the actual secrets stay in Kubernetes
Secrets.

## Configuration

| Parameter | Description | Default |
|-----------|-------------|---------|
| `replicaCount` | Number of replicas (1 by default; see Multi-Instance Deployment) | `1` |
| `strategy` | Deployment update strategy (defaults to `Recreate`) | unset |
| `autoscaling.enabled` | Create a HorizontalPodAutoscaler (requires multi-instance setup) | `false` |
| `image.repository` | Container image repository | `ghcr.io/volcengine/openviking` |
| `image.tag` | Container image tag (empty resolves to `appVersion`) | `""` |
| `image.pullPolicy` | Image pull policy | `IfNotPresent` |
| `service.type` | Kubernetes service type | `ClusterIP` |
| `service.port` | Service port | `1933` |
| `persistence.enabled` | Enable persistent storage | `true` |
| `persistence.size` | PVC size | `20Gi` |
| `persistence.storageClass` | Storage class name | `""` (default) |
| `persistence.mountPath` | Container path for OpenViking persistent state | `/app/.openviking` |
| `bot.enabled` | Start vikingbot alongside the API server | `false` |
| `persistence.existingClaim` | Use an existing PVC | `""` |
| `resources.limits.cpu` | CPU limit | `2` |
| `resources.limits.memory` | Memory limit | `4Gi` |
| `resources.requests.cpu` | CPU request | `500m` |
| `resources.requests.memory` | Memory request | `1Gi` |
| `ingress.enabled` | Enable ingress | `false` |
| `config.server.root_api_key` | API key required when server binds to 0.0.0.0; **fail-fast if missing** | `null` |
| `config` | Full ov.conf configuration object | See `values.yaml` |
| `config.server.observability` | OpenTelemetry exporters (metrics/traces/logs); disabled by default | See `values.yaml` |
| `serviceAccount.create` | Create a dedicated ServiceAccount for the pod | `false` |
| `serviceAccount.name` | Name of the ServiceAccount to use (generated when `create: true` and unset) | `""` |
| `extraEnv` | Additional environment variables | `[]` |
| `monitoring.serviceMonitor.enabled` | Create a Prometheus ServiceMonitor for the `/metrics` endpoint | `false` |
| `monitoring.serviceMonitor.labels` | Extra labels for ServiceMonitor (set the Prometheus release label here) | `{}` |
| `monitoring.serviceMonitor.relabelings` | Target relabeling rules | `[]` |
| `monitoring.serviceMonitor.metricRelabelings` | Metric-level relabeling rules | `[]` |
| `podDisruptionBudget.enabled` | Create a PodDisruptionBudget (multi-instance only) | `false` |
| `networkPolicy.enabled` | Create a NetworkPolicy restricting ingress | `false` |
| `networkPolicy.ingress` | Custom ingress rules (default allows http from any source) | unset |

## Observability

OpenViking exposes a Prometheus `/metrics` endpoint (enabled by default in the
app) and supports OpenTelemetry exporters for metrics, traces, and logs.

### Prometheus (ServiceMonitor)

If you run the Prometheus operator, enable the ServiceMonitor and set the label
that matches your Prometheus release:

```yaml
monitoring:
  serviceMonitor:
    enabled: true
    labels:
      release: prometheus  # match your Prometheus Helm release
```

### OpenTelemetry

OTel exporters are disabled by default in `values.yaml` under
`config.server.observability`. Enable the signal(s) you need and point them at
your OTel Collector:

```yaml
config:
  server:
    observability:
      metrics:
        enabled: true
        exporters:
          otel:
            enabled: true
            endpoint: "otel-collector.monitoring.svc.cluster.local:4317"
      traces:
        enabled: true
        endpoint: "otel-collector.monitoring.svc.cluster.local:4317"
      logs:
        enabled: true
        endpoint: "otel-collector.monitoring.svc.cluster.local:4317"
```

### ServiceMonitor relabeling

The ServiceMonitor supports `relabelings` (target relabeling) and
`metricRelabelings` (metric-level filtering) for advanced Prometheus setups:

```yaml
monitoring:
  serviceMonitor:
    enabled: true
    labels:
      release: prometheus
    relabelings:
      - sourceLabels: [__address__]
        targetLabel: cluster
        replacement: my-cluster
    metricRelabelings:
      - action: drop
        sourceLabels: [__name__]
        regex: 'openviking_encryption_.*'
```

## Upgrading

```bash
helm upgrade openviking ./deploy/helm/openviking -f my-values.yaml
```

The default strategy is `Recreate` (the pod is terminated and recreated on each
upgrade). This is the safe choice for single-replica with the local backend.
For multi-instance deployments, set `strategy.type: RollingUpdate` to avoid
downtime during upgrades.

## Multi-Instance Deployment

By default the chart runs a single replica with local backends. OpenViking
supports multi-instance when pods share the same data — either via **remote
backends** or a **shared filesystem**. See the
[official deployment guide](https://docs.openviking.ai/zh/guides/03-deployment#多实例部署注意事项)
for the full reference.

### Option A: Remote backends (recommended for cloud)

AGFS and VectorDB both use cloud services, so all pods access the same data
regardless of PVC:

```yaml
config:
  storage:
    vectordb:
      backend: "volcengine"   # or "qdrant", "http", etc.
    agfs:
      backend: "s3"
```

> **Both backends must be remote.** Setting only `agfs.backend: "s3"` while
> leaving `vectordb.backend: "local"` causes each pod's vector index (RocksDB)
> to live on its own PVC — indexes silently diverge.

### Option B: Shared filesystem (NFS / CephFS / RWX PVC)

Keep `local` backends but mount a shared `ReadWriteMany` volume so all pods
read/write the same workspace:

```yaml
persistence:
  accessMode: ReadWriteMany
  storageClass: "nfs-client"   # your RWX storage class
```

### Required ov.conf settings (both options)

```yaml
config:
  server:
    temp_upload:
      default_mode: "shared"
    observability:
      usage_audit:
        sqlite_path: "/var/lib/openviking-local/usage_audit.sqlite3"
  storage:
    skip_process_lock: true            # REQUIRED — skip .openviking.pid
    agfs:
      queuefs:
        db_path: "/var/lib/openviking-local/queue.db"  # per-pod SQLite
```

### Chart guards

The chart fails fast if multi-instance is requested without the prerequisites:

1. **`skip_process_lock=true`** is required (the `.openviking.pid` process lock
   prevents concurrent access by design).
2. **Shared data** is required — either remote backends for BOTH agfs and
   vectordb, OR `persistence.accessMode: ReadWriteMany` for a shared volume.
   With local backends on a `ReadWriteOnce` PVC, each pod gets divergent data.

### Enabling HPA and PDB

Once the prerequisites are met:

```yaml
replicaCount: 1
autoscaling:
  enabled: true
  minReplicas: 2
  maxReplicas: 5
  targetCPUUtilizationPercentage: 80
strategy:
  type: RollingUpdate
  rollingUpdate:
    maxUnavailable: 0
    maxSurge: 1
podDisruptionBudget:
  enabled: true
  minAvailable: 1
```

## Uninstalling

```bash
helm uninstall openviking
```

The PersistentVolumeClaim is annotated with `helm.sh/resource-policy: keep`, so
it survives `helm uninstall` and protects your data from accidental removal. To
reclaim the storage after uninstalling:

```bash
kubectl delete pvc openviking-data
```
