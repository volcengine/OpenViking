# OpenViking Helm Chart

This Helm chart deploys OpenViking on Kubernetes, providing a scalable and production-ready RAG (Retrieval-Augmented Generation) and semantic search service.

## Overview

[OpenViking](https://github.com/volcengine/OpenViking) is an open-source RAG and semantic search engine that serves as a Context Database MCP (Model Context Protocol) server. This Helm chart enables easy deployment on Kubernetes clusters with support for major cloud providers.

## Prerequisites

- Kubernetes 1.24+
- Helm 3.8+
- A valid Volcengine API key for embedding and VLM services

## Installation

### Add the Helm repository (when published)

```bash
helm repo add openviking https://volcengine.github.io/openviking
helm repo update
```

### Install from local chart

```bash
# Clone the repository
git clone https://github.com/volcengine/OpenViking.git
cd OpenViking/deploy/helm

# Install with default values
helm install openviking ./openviking

# Install with custom values
helm install openviking ./openviking -f my-values.yaml
```

### Quick Start

```bash
# GCP deployment
helm install openviking ./openviking \
  --set cloudProvider=gcp \
  --set openviking.config.embedding.dense.api_key=YOUR_API_KEY

# AWS deployment
helm install openviking ./openviking \
  --set cloudProvider=aws \
  --set openviking.config.embedding.dense.api_key=YOUR_API_KEY
```

## Configuration

### Cloud Provider Support

The chart supports automatic LoadBalancer annotation configuration for major cloud providers:

| Provider | Configuration Value |
|----------|-------------------|
| Google Cloud Platform | `cloudProvider: gcp` |
| Amazon Web Services | `cloudProvider: aws` |
| Other/Generic | `cloudProvider: ""` (default) |

### Key Configuration Options

| Parameter | Description | Default |
|-----------|-------------|---------|
| `cloudProvider` | Cloud provider for LoadBalancer annotations | `""` |
| `replicaCount` | Number of replicas | `1` |
| `image.repository` | Container image repository | `ghcr.io/astral-sh/uv` |
| `image.tag` | Container image tag | `python3.12-bookworm` |
| `service.type` | Kubernetes service type | `LoadBalancer` |
| `service.port` | Service port | `1933` |
| `openviking.config.server.api_key` | API key for authentication | `null` |
| `openviking.config.embedding.dense.api_key` | Volcengine API key | `null` |

### OpenViking Configuration

All OpenViking configuration options from `ov.conf` are available under `openviking.config`. See `values.yaml` for the complete default configuration.

### Embedding Configuration

The embedding service requires a Volcengine API key:

```yaml
openviking:
  config:
    embedding:
      dense:
        api_key: "your-api-key-here"
        api_base: "https://ark.cn-beijing.volces.com/api/v3"
        model: "doubao-embedding-vision-250615"
```

### VLM Configuration

For vision-language model support:

```yaml
openviking:
  config:
    vlm:
      api_key: "your-api-key-here"
      api_base: "https://ark.cn-beijing.volces.com/api/v3"
      model: "doubao-seed-1-8-251228"
```

## Storage

### Default (emptyDir)

By default, the chart uses `emptyDir` volumes for data storage. This is suitable for development and testing but **data will be lost** when pods are restarted.

### Persistent Storage (Optional)

To enable persistent storage with PVC:

```yaml
openviking:
  dataVolume:
    enabled: true
    usePVC: true
    size: 50Gi
    storageClassName: standard
    accessModes:
      - ReadWriteOnce
```

## Security

### API Key Authentication

Enable API key authentication to secure your OpenViking server:

```yaml
openviking:
  config:
    server:
      api_key: "your-secure-api-key"
      cors_origins:
        - "https://your-domain.com"
```

### Secrets Management

For production deployments, use Kubernetes secrets or external secret management:

```bash
# Create secret from literal
kubectl create secret generic openviking-config \
  --from-literal=ov.conf='{"server":{"api_key":"secret"}}'

# Or mount existing secret
helm install openviking ./openviking \
  --set existingSecret=openviking-config
```

## Autoscaling

Enable Horizontal Pod Autoscaler for production workloads:

```yaml
autoscaling:
  enabled: true
  minReplicas: 2
  maxReplicas: 10
  targetCPUUtilizationPercentage: 80
  targetMemoryUtilizationPercentage: 80
```

## Resource Limits

Default resource configuration:

```yaml
resources:
  limits:
    cpu: 2000m
    memory: 4Gi
  requests:
    cpu: 500m
    memory: 1Gi
```

Adjust based on your workload requirements.

## Usage Examples

### Connect with CLI

```bash
# Get the LoadBalancer IP
export OPENVIKING_IP=$(kubectl get svc openviking -o jsonpath='{.status.loadBalancer.ingress[0].ip}')

# Create CLI configuration
cat > ~/.openviking/ovcli.conf <<EOF
{
  "url": "http://$OPENVIKING_IP:1933",
  "api_key": null,
  "output": "table"
}
EOF

# Test connection
openviking health
```

### Python Client

```python
import openviking as ov

# Get service endpoint
# kubectl get svc openviking

client = ov.OpenViking(url="http://<load-balancer-ip>:1933", api_key="your-key")
client.initialize()

# Add a resource
client.add_resource(path="./document.pdf")
client.wait_processed()

# Search
results = client.find("your search query")
print(results)

client.close()
```

## Troubleshooting

### Pod fails to start

Check the pod logs:
```bash
kubectl logs -l app.kubernetes.io/name=openviking
```

### Health check fails

Verify the configuration:
```bash
kubectl get secret openviking-config -o jsonpath='{.data.ov\.conf}' | base64 -d
```

### LoadBalancer not getting IP

Wait for the cloud provider to provision the load balancer:
```bash
kubectl get svc openviking -w
```

Check cloud provider-specific annotations in `values.yaml`.

## Uninstallation

```bash
helm uninstall openviking
```

To remove persistent data (if PVC was enabled):
```bash
kubectl delete pvc openviking-data
```

## Contributing

Contributions are welcome! Please see the [OpenViking repository](https://github.com/volcengine/OpenViking) for contribution guidelines.

## License

This Helm chart is licensed under the Apache License 2.0, matching the OpenViking project license.
