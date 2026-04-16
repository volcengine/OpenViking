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
  --set config.server.root_api_key="YOUR_ROOT_API_KEY" \
  --set config.embedding.dense.api_key="YOUR_VOLCENGINE_API_KEY" \
  --set config.vlm.api_key="YOUR_VOLCENGINE_API_KEY"
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
    workspace: /app/data/openviking_workspace
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
    model: "doubao-seed-2-0-pro-260215"
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
  --from-literal=embedding-api-key="YOUR_KEY" \
  --from-literal=vlm-api-key="YOUR_KEY"
```

Then reference it in your values:

```yaml
extraEnv:
  - name: EMBEDDING_API_KEY
    valueFrom:
      secretKeyRef:
        name: openviking-api-keys
        key: embedding-api-key
  - name: VLM_API_KEY
    valueFrom:
      secretKeyRef:
        name: openviking-api-keys
        key: vlm-api-key
```

## Configuration

| Parameter | Description | Default |
|-----------|-------------|---------|
| `replicaCount` | Number of replicas | `1` |
| `image.repository` | Container image repository | `ghcr.io/volcengine/openviking` |
| `image.tag` | Container image tag | Chart appVersion |
| `image.pullPolicy` | Image pull policy | `IfNotPresent` |
| `service.type` | Kubernetes service type | `ClusterIP` |
| `service.port` | Service port | `1933` |
| `persistence.enabled` | Enable persistent storage | `true` |
| `persistence.size` | PVC size | `20Gi` |
| `persistence.storageClass` | Storage class name | `""` (default) |
| `persistence.existingClaim` | Use an existing PVC | `""` |
| `resources.limits.cpu` | CPU limit | `2` |
| `resources.limits.memory` | Memory limit | `4Gi` |
| `resources.requests.cpu` | CPU request | `500m` |
| `resources.requests.memory` | Memory request | `1Gi` |
| `ingress.enabled` | Enable ingress | `false` |
| `config.server.root_api_key` | API key required when server binds to 0.0.0.0 | `""` |
| `config` | Full ov.conf configuration object | See `values.yaml` |
| `extraEnv` | Additional environment variables | `[]` |

## Upgrading

```bash
helm upgrade openviking ./deploy/helm/openviking -f my-values.yaml
```

The deployment uses a `Recreate` strategy to avoid data corruption from
multiple pods accessing the same RocksDB volume simultaneously.

## Uninstalling

```bash
helm uninstall openviking
```

Note: The PersistentVolumeClaim is not deleted automatically. To remove stored
data:

```bash
kubectl delete pvc openviking-data
```
