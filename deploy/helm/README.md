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

Set `gateway.enabled: true` to run [OpenViking Gateway](https://docs.openviking.ai/en/guides/15-gateway)
inside the server container. The chart adds `gateway.enabled` to `ov.conf` and stores
gateway data in `<persistence.mountPath>/gateway` on the persistent volume. There is no
sidecar and no extra Service port: clients use `/gateway` on the server port, and the existing Ingress
forwards every path to OpenViking.

Model requests stream, so the Ingress must turn off response buffering and allow request
bodies of at least 32 MiB and timeouts of 600 seconds. The chart does not set these
annotations for you. With NGINX:

```yaml
gateway:
  enabled: true
  existingSecret: ""  # optional: Secret with a management-key entry
config:
  server:
    auth_mode: api_key
  gateway:
    public_url: https://ov.example.com
ingress:
  enabled: true
  className: nginx
  annotations:
    nginx.ingress.kubernetes.io/proxy-buffering: "off"
    nginx.ingress.kubernetes.io/proxy-request-buffering: "off"
    nginx.ingress.kubernetes.io/proxy-body-size: 32m
    nginx.ingress.kubernetes.io/proxy-read-timeout: "600"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "600"
  hosts:
    - host: ov.example.com
      paths:
        - path: /
          pathType: Prefix
```

Set `config.gateway.public_url` to the address clients use, without `/gateway`; Studio's
setup guides and file upload links are built from it, with `/gateway` appended. Clients use
`https://ov.example.com/gateway/v1` (OpenAI-compatible) or `https://ov.example.com/gateway`
(Anthropic).

#### Secrets

The gateway encrypts its stored data with the server's `config.encryption` settings; with
server encryption off, it stores them unencrypted.

`gateway.existingSecret` optionally names a Secret. Both entries are optional:

- `management-key` (at least 32 characters) is passed as
  `OPENVIKING_GATEWAY_MANAGEMENT_KEY`. With it, only requests that carry this key can change
  upstreams and context profiles; see
  [Gateway management key](https://docs.openviking.ai/en/guides/22-gateway-operations#gateway-management-key).
- `encryption-key` is the v0.5.0 gateway key, passed as `OPENVIKING_GATEWAY_ENCRYPTION_KEY`.
  It is only read once, to migrate data stored by the v0.5.0 gateway.

```bash
kubectl create secret generic openviking-gateway \
  --from-literal=management-key="$(openssl rand -base64 32)"
```

Keep `replicaCount: 1`: the gateway's SQLite databases serve one pod only. The default
local vectordb also locks the workspace to a single worker, so raise the server's `workers`
setting only after switching to a remote vectordb backend such as VikingDB.

#### Upgrading from the sidecar chart

- Keep the persistent volume. On first start the server re-encrypts the gateway's stored
  data with its own encryption settings, reading the old key from the `encryption-key`
  entry of `gateway.existingSecret` or from `encryption.key` in the gateway storage
  directory. If old encrypted data exists and neither is present, the server refuses to start.
  Keep the `encryption-key` entry for the upgrade release only; after the server has started
  once, the key is no longer read and you can remove the entry.
- Remove `gateway.port`, `gateway.workers` and `gateway.resources`.
- Remove `host`, `port`, `workers`, `url`, `openviking_url` and `admin_token_env` from
  `config.gateway`. The server ignores them with a startup warning.
- Point `config.gateway.public_url` at the server's public address, not port 1935, and
  move clients to the server port and the `/gateway` prefix: `http://host:1935/v1` becomes
  `https://ov.example.com/gateway/v1`. The management API moved from
  `/api/v1/admin/gateway/...` to `/gateway/admin/...`.
- The shared admin token is gone and does not act as a management key. To restrict
  changes to upstreams and context profiles, add a `management-key` entry to
  `gateway.existingSecret`.

For deployment, Studio management, backups and troubleshooting, see
[OpenViking Gateway deployment and operations](https://docs.openviking.ai/en/guides/22-gateway-operations).

## Standalone OpenViking Gateway

Use `deploy/helm/openviking-gateway` when the gateway needs independent replicas,
rollouts and a Service. The `openviking` chart's `gateway.enabled` runs inside the
OpenViking Server and follows its single-replica lifecycle; it is still the
simplest choice when you operate both together.

The standalone chart targets unreleased gateway features: until `appVersion`
names a published release, `image.tag` is required and must name a compatible build.
To release the chart, set `appVersion` to the release without the `v` and bump `version`,
then run `PYTHONPATH=. python scripts/gateway_chart_check.py` and the gateway kind suite
(`tests/gateway/e2e`) against that release image.

```bash
helm upgrade --install gateway ./deploy/helm/openviking-gateway \
  --set-string image.tag='<gateway-image-tag>' \
  --set-string config.openviking_url=https://ov.example.com
```

By default this creates a single SQLite replica, PVC, and ClusterIP Service on
port 80, forwarding to 1935. `config` renders a JSON `gateway.conf`; `host`, `port`
and `storage_path` are fixed by the chart. The OpenViking endpoint must use API-key
authentication. A default StorageClass is sufficient, or select one with
`persistence.storageClass` or use `persistence.existingClaim`.

Set both `config.storage.kernel` and `config.storage.management` to `redis`, and
set `config.storage.redis` (endpoints, `password_env: OPENVIKING_GATEWAY_REDIS_PASSWORD`),
before increasing `replicaCount`. Redis deployments use RollingUpdate and do
not create a PVC; multiple replicas get a PDB. SQLite uses Recreate and rejects
multiple replicas. Disabling persistence uses emptyDir and loses SQLite state
when the Pod is replaced. PVCs are retained after uninstall.

Use `existingSecret` entries `management-key`, `redis-password` and `root-key`
for the management credential, Redis password and encryption root key. Local
and KMS encryption require a pre-generated root key, shared read-only by all
replicas. Other credentials use `$VAR` references in `config` and `extraEnv`
with `secretKeyRef`. External Secret changes require a Deployment restart.

The chart provides no Ingress. Its Service and Deployment share a name; use
`fullnameOverride` to pin it. The stable selectors are `app.kubernetes.io/name`
and `app.kubernetes.io/instance`. `service.type`, `service.loadBalancerSourceRanges`,
and optional `networkPolicy.ingressFrom` support external routing. All probes
use `/health`; startup allows 300 seconds, preStop sleeps 15 seconds, and the
default termination grace is 660 seconds.

For Redis settings, encryption key creation, a complete multi-replica values
example, shutdown timing and operations, see the [gateway operations guide](../../docs/en/guides/22-gateway-operations.md#helm).

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
| `gateway.enabled` | Run OpenViking Gateway inside the server container, under `/gateway` on the server port | `false` |
| `gateway.existingSecret` | Optional Secret with optional entries `management-key` (required to change upstreams and context profiles) and `encryption-key` (v0.5.0 key, read once for migration) | `""` |

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
