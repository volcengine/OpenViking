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

The chart deploys `ghcr.io/volcengine/openviking:latest` by default. To choose
a different image tag:

```bash
# newest image from the main branch
helm upgrade --install openviking ./deploy/helm/openviking --set image.tag=main

# pinned release image
helm upgrade --install openviking ./deploy/helm/openviking --set image.tag=v0.3.17

# use the default latest image
helm upgrade --install openviking ./deploy/helm/openviking --set image.tag=
```

We recommend the `ghcr.io` image. If `ghcr.io` is hard to reach, add
`--set image.repository=openviking-cn-beijing.cr.volces.com/volcengine/openviking`.

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

### OpenViking Gateway

The chart can run [OpenViking Gateway](https://docs.openviking.ai/en/guides/15-gateway)
as a second container in the OpenViking pod, sharing its volume and `ov.conf`. The gateway
requires OpenViking Server to use API key authentication.

Create a Secret with the fixed keys `encryption-key` and `admin-token`:

```bash
kubectl create secret generic openviking-gateway \
  --from-literal=encryption-key="$(python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')" \
  --from-literal=admin-token="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
```

Back up the encryption key along with the volume. The gateway can read its stored data only
with this exact key, and the key can't be rotated.

Then enable the gateway in your values:

```yaml
gateway:
  enabled: true
  existingSecret: openviking-gateway
  workers: 2

config:
  server:
    auth_mode: api_key
  gateway:
    public_url: https://ov.example.com
```

- The chart generates the rest of `config.gateway`: bind address, port, workers,
  internal URLs and a `storage_path` on the data volume. Values you set under
  `config.gateway` take precedence.
- Set `public_url` to the address clients use, written out literally. The gateway does not
  expand `${VAR}` placeholders, and its container does not receive `extraEnv`.
- With the ingress enabled, the chart routes `/v1`, `/api/v3`, `/api/compatible/v1` and
  `/gateway/uploads` to the gateway. It never routes the gateway's `/admin` paths;
  keep it that way in your own ingress rules.
- Keep `replicaCount: 1`. The gateway's storage must be used from a single host; scale with
  `gateway.workers` instead.

The chart does not set ingress annotations. Model requests stream and can be large, so turn off
proxy buffering and allow 32 MiB request bodies and 600-second read timeouts. For the NGINX
annotations, Studio setup and troubleshooting, see
[OpenViking Gateway deployment and operations](https://docs.openviking.ai/en/guides/22-gateway-operations#helm).

## Configuration

| Parameter | Description | Default |
|-----------|-------------|---------|
| `replicaCount` | Number of replicas | `1` |
| `image.repository` | Container image repository | `ghcr.io/volcengine/openviking` |
| `image.tag` | Container image tag (`latest`, `main`, pinned release, or empty for `latest`) | `latest` |
| `image.pullPolicy` | Image pull policy | `Always` |
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
| `config.server.root_api_key` | API key required when server binds to 0.0.0.0 | `""` |
| `config` | Full ov.conf configuration object | See `values.yaml` |
| `extraEnv` | Additional environment variables | `[]` |
| `gateway.enabled` | Run OpenViking Gateway as a second container in the pod | `false` |
| `gateway.existingSecret` | Secret with `encryption-key` and `admin-token`; required when enabled | `""` |
| `gateway.workers` | Gateway worker processes | `2` |
| `gateway.port` | Gateway container and Service port | `1935` |
| `gateway.resources` | Gateway container resources | `{}` |

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

Note: The PersistentVolumeClaim is retained by Helm and becomes orphaned after
uninstall. Delete it explicitly to remove the stored data:

```bash
kubectl delete pvc openviking-data
```
