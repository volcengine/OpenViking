# OpenViking Helm Chart

此 Helm Chart 用于在 Kubernetes 上部署 OpenViking，提供可扩展、生产就绪的 RAG（检索增强生成）和语义搜索服务。

## 概述

[OpenViking](https://github.com/volcengine/OpenViking) 是一个开源的 RAG 和语义搜索引擎，作为上下文数据库 MCP（Model Context Protocol）服务器运行。此 Helm Chart 支持在 Kubernetes 集群上轻松部署，兼容主流云服务商。

## 前置条件

- Kubernetes 1.24+
- Helm 3.8+
- 有效的火山引擎 API Key（用于 embedding 和 VLM 服务）

## 安装

### 添加 Helm 仓库（发布后可用）

```bash
helm repo add openviking https://volcengine.github.io/openviking
helm repo update
```

### 从本地 Chart 安装

```bash
# 克隆仓库
git clone https://github.com/volcengine/OpenViking.git
cd OpenViking/deploy/helm

# 使用默认值安装
helm install openviking ./openviking

# 使用自定义值安装
helm install openviking ./openviking -f my-values.yaml
```

### 快速开始

```bash
# GCP 部署
helm install openviking ./openviking \
  --set cloudProvider=gcp \
  --set openviking.config.embedding.dense.api_key=YOUR_API_KEY

# AWS 部署
helm install openviking ./openviking \
  --set cloudProvider=aws \
  --set openviking.config.embedding.dense.api_key=YOUR_API_KEY
```

## 配置

### 云服务商支持

此 Chart 支持为主流云服务商自动配置 LoadBalancer 注解：

| 云服务商 | 配置值 |
|----------|--------|
| Google Cloud Platform | `cloudProvider: gcp` |
| Amazon Web Services | `cloudProvider: aws` |
| 其他/通用 | `cloudProvider: ""`（默认） |

### 关键配置选项

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `cloudProvider` | 云服务商，用于 LoadBalancer 注解 | `""` |
| `replicaCount` | 副本数量 | `1` |
| `image.repository` | 容器镜像仓库 | `ghcr.io/astral-sh/uv` |
| `image.tag` | 容器镜像标签 | `python3.12-bookworm` |
| `service.type` | Kubernetes 服务类型 | `LoadBalancer` |
| `service.port` | 服务端口 | `1933` |
| `openviking.config.server.api_key` | 认证 API Key | `null` |
| `openviking.config.embedding.dense.api_key` | 火山引擎 API Key | `null` |

### OpenViking 配置

`ov.conf` 中的所有 OpenViking 配置选项都在 `openviking.config` 下可用。完整默认配置请参见 `values.yaml`。

### Embedding 配置

Embedding 服务需要火山引擎 API Key：

```yaml
openviking:
  config:
    embedding:
      dense:
        api_key: "your-api-key-here"
        api_base: "https://ark.cn-beijing.volces.com/api/v3"
        model: "doubao-embedding-vision-251215"
```

### VLM 配置

视觉语言模型支持：

```yaml
openviking:
  config:
    vlm:
      api_key: "your-api-key-here"
      api_base: "https://ark.cn-beijing.volces.com/api/v3"
      model: "doubao-seed-2-0-pro-260215"
```

## 存储

### 默认（emptyDir）

默认情况下，Chart 使用 `emptyDir` 卷进行数据存储。这适用于开发和测试，但 Pod 重启后**数据将丢失**。

### 持久化存储（可选）

使用 PVC 启用持久化存储：

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

## 安全

### API Key 认证

启用 API Key 认证以保护 OpenViking 服务器：

```yaml
openviking:
  config:
    server:
      api_key: "your-secure-api-key"
      cors_origins:
        - "https://your-domain.com"
```

### 密钥管理

生产环境建议使用 Kubernetes Secrets 或外部密钥管理：

```bash
# 从字面值创建 Secret
kubectl create secret generic openviking-config \
  --from-literal=ov.conf='{"server":{"api_key":"secret"}}'

# 或挂载现有 Secret
helm install openviking ./openviking \
  --set existingSecret=openviking-config
```

## 自动扩缩容

为生产工作负载启用 Horizontal Pod Autoscaler：

```yaml
autoscaling:
  enabled: true
  minReplicas: 2
  maxReplicas: 10
  targetCPUUtilizationPercentage: 80
  targetMemoryUtilizationPercentage: 80
```

## 资源限制

默认资源配置：

```yaml
resources:
  limits:
    cpu: 2000m
    memory: 4Gi
  requests:
    cpu: 500m
    memory: 1Gi
```

根据工作负载需求调整。

## 使用示例

### 使用 CLI 连接

```bash
# 获取 LoadBalancer IP
export OPENVIKING_IP=$(kubectl get svc openviking -o jsonpath='{.status.loadBalancer.ingress[0].ip}')

# 创建 CLI 配置
cat > ~/.openviking/ovcli.conf <<EOF
{
  "url": "http://$OPENVIKING_IP:1933",
  "api_key": null,
  "output": "table"
}
EOF

# 测试连接
openviking health
```

### Python 客户端

```python
import openviking as ov

# 获取服务端点
# kubectl get svc openviking

client = ov.OpenViking(url="http://<load-balancer-ip>:1933", api_key="your-key")
client.initialize()

# 添加资源
client.add_resource(path="./document.pdf")
client.wait_processed()

# 搜索
results = client.find("your search query")
print(results)

client.close()
```

## 故障排除

### Pod 启动失败

检查 Pod 日志：
```bash
kubectl logs -l app.kubernetes.io/name=openviking
```

### 健康检查失败

验证配置：
```bash
kubectl get secret openviking-config -o jsonpath='{.data.ov\.conf}' | base64 -d
```

### LoadBalancer 未获取 IP

等待云服务商分配负载均衡器：
```bash
kubectl get svc openviking -w
```

检查 `values.yaml` 中云服务商特定的注解。

## 卸载

```bash
helm uninstall openviking
```

删除持久化数据（如果启用了 PVC）：
```bash
kubectl delete pvc openviking-data
```

## 贡献

欢迎贡献！请参见 [OpenViking 仓库](https://github.com/volcengine/OpenViking) 的贡献指南。

## 许可证

此 Helm Chart 采用 Apache License 2.0 许可证，与 OpenViking 项目许可证一致。