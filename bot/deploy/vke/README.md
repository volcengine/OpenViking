# VKE 部署指南

本文档介绍如何将 Vikingbot 部署到火山引擎容器服务（VKE）。

## 目录

- [架构概述](#架构概述)
- [前置准备](#前置准备)
  - [1. 火山引擎账号](#1-火山引擎账号)
  - [2. 创建 VKE 集群](#2-创建-vke-集群)
  - [3. 创建容器镜像仓库](#3-创建容器镜像仓库)
  - [4. 创建 TOS 存储桶（可选）](#4-创建-tos-存储桶可选)
  - [5. 获取访问密钥](#5-获取访问密钥)
  - [6. 配置本地环境](#6-配置本地环境)
- [快速部署](#快速部署)
- [配置详解](#配置详解)
- [手动部署](#手动部署)
- [验证部署](#验证部署)
- [故障排查](#故障排查)

---

## 架构概述

```
┌─────────────────────────────────────────────────────────────┐
│                      火山引擎 VKE                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │                   Namespace: default                   │  │
│  │  ┌─────────────────────────────────────────────────┐  │  │
│  │  │              Deployment: vikingbot               │  │  │
│  │  │  ┌───────────────────────────────────────────┐  │  │  │
│  │  │  │  Pod (2 replicas)                         │  │  │  │
│  │  │  │  ┌─────────────────────────────────────┐  │  │  │  │
│  │  │  │  │  Container: vikingbot               │  │  │  │  │
│  │  │  │  │  - Port: 18791 (gateway)           │  │  │  │  │
│  │  │  │  │  - Volume: /root/.vikingbot         │  │  │  │  │
│  │  │  │  └─────────────────────────────────────┘  │  │  │  │
│  │  │  └───────────────────────────────────────────┘  │  │  │
│  │  └─────────────────────────────────────────────────┘  │  │
│  │                                                           │  │
│  │  ┌─────────────────────────────────────────────────┐  │  │
│  │  │  Service: vikingbot (ClusterIP)                │  │  │
│  │  │  - Port: 80 → TargetPort: 18791               │  │  │
│  │  └─────────────────────────────────────────────────┘  │  │
│  │                                                           │  │
│  │  ┌─────────────────────────────────────────────────┐  │  │
│  │  │  PVC: vikingbot-data (10Gi)                    │  │  │
│  │  │  └──→ PV: vikingbot-tos-pv (TOS)              │  │  │
│  │  └─────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                               │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  TOS Bucket: vikingbot_data                          │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 前置准备

### 1. 火山引擎账号

- 注册火山引擎账号：https://www.volcengine.com/
- 完成实名认证
- 开通以下服务：
  - **容器服务 VKE**
  - **容器镜像服务 CR**
  - **对象存储 TOS**（可选，用于持久化存储）

---

### 2. 创建 VKE 集群

1. 登录火山引擎控制台
2. 进入 **容器服务 VKE** → **集群**
3. 点击 **创建集群**
4. 配置集群参数：
   - **集群名称**：vikingbot（或自定义）
   - **Kubernetes 版本**：选择最新稳定版（推荐 1.24+）
   - **容器运行时**：containerd
   - **网络模式**：Flannel 或 Calico
   - **Service CIDR**：默认即可
5. 配置节点池：
   - **节点规格**：推荐 2核4G 或更高（ecs.g1.large）
   - **节点数量**：至少 2 个节点
   - **系统盘**：40Gi SSD
6. 确认配置并创建集群

> **等待集群创建完成**（约 10-15 分钟）

---

### 3. 创建容器镜像仓库

1. 进入 **容器镜像服务 CR** → **命名空间**
2. 点击 **创建命名空间**
   - 名称：`vikingbot`
   - 类型：私有
3. 进入 **镜像仓库**
4. 点击 **创建镜像仓库**
   - 名称：`vikingbot`
   - 命名空间：选择刚才创建的 `vikingbot`
   - 描述：Vikingbot 镜像仓库

---

### 4. 创建 TOS 存储桶（可选）

如果使用 TOS 作为持久化存储，需要创建存储桶：

1. 进入 **对象存储 TOS** → **存储桶列表**
2. 点击 **创建存储桶**
3. 配置参数：
   - **名称**：`vikingbot-data`（或自定义）
   - **地域**：选择与 VKE 集群相同的地域（如 cn-beijing）
   - **存储类型**：标准存储
   - **权限**：私有
4. 点击 **确定** 创建

---

### 5. 获取访问密钥

#### 5.1 创建 AccessKey

1. 鼠标悬停在右上角头像，点击 **API 访问密钥**
2. 点击 **新建密钥**
3. 完成手机验证
4. 保存生成的 **AccessKey ID** 和 **Secret Access Key**

> **重要**：Secret Access Key 只显示一次，请妥善保存！

#### 5.2 获取 Kubeconfig

1. 进入 **容器服务 VKE** → **集群**
2. 找到你的集群，点击 **连接**
3. 在 **集群访问凭证** 页签，点击 **下载** 获取 Kubeconfig
4. 将下载的文件保存到 `~/.kube/config`，或配置 `KUBECONFIG` 环境变量

验证连接：
```bash
kubectl get nodes
```

---

### 6. 配置本地环境

确保本地已安装：

- **Docker**：用于构建镜像
- **kubectl**：用于操作 Kubernetes 集群
- **Python 3**：部署脚本需要

验证安装：
```bash
docker --version
kubectl version --client
python3 --version
```

---

## 快速部署

### 步骤 1：复制配置文件

```bash
mkdir -p ~/.config/vikingbot
cp deploy/vke/vke_deploy.example.yaml ~/.config/vikingbot/vke_deploy.yaml
```

### 步骤 2：编辑配置

```bash
vim ~/.config/vikingbot/vke_deploy.yaml
```

填入以下信息：

```yaml
volcengine_access_key: AKLTxxxxxxxxxx      # 你的 AccessKey ID
volcengine_secret_key: xxxxxxxxxx          # 你的 Secret Access Key
volcengine_region: cn-beijing               # 地域

vke_cluster_id: ccxxxxxxxxxx                # 集群 ID（从控制台获取）

image_registry: vikingbot-cn-beijing.cr.volces.com  # 镜像仓库地址
image_namespace: vikingbot
image_repository: vikingbot
image_tag: latest

# 镜像仓库登录凭证（如果是私有仓库）
registry_username: "你的火山引擎账号"
registry_password: "你的火山引擎密码"

# 存储类型：local (EBS) 或 tos
storage_type: tos

# TOS 配置（仅 storage_type=tos 时需要）
tos_bucket: vikingbot_data
tos_path: /.vikingbot/
tos_region: cn-beijing
```

### 步骤 3：执行部署

```bash
cd /path/to/vikingbot
chmod +x deploy/vke/deploy.sh
deploy/vke/deploy.sh
```

部署脚本会自动完成：
1. 构建 Docker 镜像
2. 推送镜像到火山引擎 CR
3. 创建 K8s 资源（Secret、PV、PVC、Deployment、Service）
4. 等待部署完成

---

## 配置详解

### vke_deploy.yaml 配置项

| 配置项 | 说明 | 必填 | 示例 |
|--------|------|------|------|
| `volcengine_access_key` | 火山引擎 AccessKey ID | 是 | `AKLTxxxx` |
| `volcengine_secret_key` | 火山引擎 Secret Access Key | 是 | `xxxx` |
| `volcengine_region` | 地域 | 是 | `cn-beijing` |
| `vke_cluster_id` | VKE 集群 ID | 是 | `ccxxxx` |
| `image_registry` | 镜像仓库地址 | 是 | `vikingbot-cn-beijing.cr.volces.com` |
| `image_namespace` | 命名空间 | 是 | `vikingbot` |
| `image_repository` | 仓库名称 | 是 | `vikingbot` |
| `image_tag` | 镜像标签 | 否 | `latest` |
| `use_timestamp_tag` | 使用时间戳标签 | 否 | `false` |
| `registry_username` | 镜像仓库用户名 | 否 | |
| `registry_password` | 镜像仓库密码 | 否 | |
| `storage_type` | 存储类型：`local` 或 `tos` | 否 | `local` |
| `tos_bucket` | TOS 桶名 | storage_type=tos | `vikingbot_data` |
| `tos_path` | TOS 路径 | storage_type=tos | `/.vikingbot/` |
| `tos_region` | TOS 地域 | storage_type=tos | `cn-beijing` |
| `k8s_namespace` | K8s 命名空间 | 否 | `default` |
| `k8s_replicas` | Pod 副本数 | 否 | `1` |
| `kubeconfig_path` | kubeconfig 路径 | 否 | `~/.kube/config` |

---

## 手动部署

如果不想使用一键部署脚本，可以按以下步骤手动操作。

### 1. 构建并推送镜像

```bash
# 构建镜像
docker build --platform linux/amd64 -f deploy/Dockerfile -t vikingbot .

# 登录镜像仓库
docker login vikingbot-cn-beijing.cr.volces.com -u <username> -p <password>

# Tag 镜像
docker tag vikingbot vikingbot-cn-beijing.cr.volces.com/vikingbot/vikingbot:latest

# 推送
docker push vikingbot-cn-beijing.cr.volces.com/vikingbot/vikingbot:latest
```

### 2. 准备 Kubernetes Manifest

复制 `deploy/vke/k8s/deployment.yaml`，替换以下变量：

- `__IMAGE_NAME__`：完整镜像名
- `__REPLICAS__`：副本数
- `__ACCESS_MODES__`：访问模式（`ReadWriteOnce` 或 `ReadWriteMany`）
- `__STORAGE_CLASS_CONFIG__`：StorageClass 配置
- `__VOLUME_NAME_CONFIG__`：VolumeName 配置

### 3. 创建 TOS Secret（仅使用 TOS 时）

```bash
# Base64 编码 AccessKey
AK_B64=$(echo -n "your-access-key" | base64)
SK_B64=$(echo -n "your-secret-key" | base64)

# 创建 Secret
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Secret
metadata:
  name: vikingbot-tos-secret
type: Opaque
data:
  AccessKeyId: ${AK_B64}
  SecretAccessKey: ${SK_B64}
EOF
```

### 4. 部署应用

```bash
kubectl apply -f deploy/vke/k8s/deployment.yaml
```

---

## 验证部署

### 查看 Pod 状态

```bash
kubectl get pods -l app=vikingbot
```

预期输出：
```
NAME                         READY   STATUS    RESTARTS   AGE
vikingbot-746d99fd94-xxxxx   1/1     Running   0          2m
```

### 查看 Service

```bash
kubectl get svc vikingbot
```

### 查看日志

```bash
# 查看所有 Pod 日志
kubectl logs -l app=vikingbot --tail=100

# 跟随日志
kubectl logs -f deployment/vikingbot
```

### 查看部署状态

```bash
kubectl rollout status deployment/vikingbot
```

### 访问 Gateway（本地端口转发）

```bash
kubectl port-forward svc/vikingbot 8080:80
```

然后访问：http://localhost:8080

---

## 故障排查

### Pod 无法启动

```bash
# 查看 Pod 事件
kubectl describe pod <pod-name>

# 查看日志
kubectl logs <pod-name>
```

### 镜像拉取失败

检查：
1. 镜像仓库地址是否正确
2. 镜像是否已推送
3. 仓库是否为私有，是否配置了 ImagePullSecret

### 存储挂载失败

```bash
# 查看 PVC 状态
kubectl get pvc

# 查看 PV 状态
kubectl get pv

# 查看事件
kubectl describe pvc vikingbot-data
```

### 健康检查失败

健康检查路径：`/health`，端口：`18791`

```bash
# 进入 Pod 内部检查
kubectl exec -it <pod-name> -- bash

# 在 Pod 内测试
curl http://localhost:18791/health
```

---

## 常用命令

```bash
# 扩容/缩容
kubectl scale deployment vikingbot --replicas=3

# 更新镜像
kubectl set image deployment/vikingbot vikingbot=vikingbot-cn-beijing.cr.volces.com/vikingbot/vikingbot:new-tag

# 重启 Deployment
kubectl rollout restart deployment/vikingbot

# 回滚
kubectl rollout undo deployment/vikingbot

# 删除所有资源
kubectl delete -f deploy/vke/k8s/deployment.yaml
```

---

## 附录

### 地域列表

| 地域 ID | 地域名称 |
|---------|----------|
| cn-beijing | 华北2（北京） |
| cn-shanghai | 华东2（上海） |
| cn-guangzhou | 华南1（广州） |
| cn-shenzhen | 华南2（深圳） |

### 镜像仓库地址格式

```
{namespace}-{region}.cr.volces.com
```

示例：`vikingbot-cn-beijing.cr.volces.com`

---

## 参考链接

- [火山引擎 VKE 文档](https://www.volcengine.com/docs/6460)
- [火山引擎 CR 文档](https://www.volcengine.com/docs/6420)
- [火山引擎 TOS 文档](https://www.volcengine.com/docs/6349)
- [Kubernetes 官方文档](https://kubernetes.io/docs/)
