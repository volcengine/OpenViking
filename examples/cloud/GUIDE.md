# OpenViking 云上部署指南（火山引擎）

本文档介绍如何将 OpenViking 部署到火山引擎云上，使用 TOS（对象存储）+ VikingDB（向量数据库）+ 方舟大模型作为后端。

## 概览

云上部署架构：

```
用户请求 → OpenViking Server (1933)
                ├── AGFS → TOS (S3 兼容协议，存储文件数据)
                ├── VectorDB → VikingDB (向量检索)
                ├── Embedding → 方舟 API (doubao-embedding-vision)
                └── VLM → 方舟 API (doubao-seed)
```

> **地域说明**：TOS 和 VikingDB 均需要选择地域（region），不同地域对应不同的服务域名。所有云服务应部署在同一地域以降低网络延迟。目前支持的地域包括 `cn-beijing`、`cn-shanghai`、`cn-guangzhou` 等，本文以 `cn-beijing` 为例。

## 前置条件

- 火山引擎账号（[注册地址](https://console.volcengine.com/)）
- 已安装 OpenViking（`pip install openviking` 或从源码安装）
- Python 3.11+

---

## 1. 开通云服务

### 1.1 开通 TOS（对象存储）

TOS 用于持久化存储 OpenViking 的文件数据（AGFS 后端）。

1. 登录 [火山引擎控制台](https://console.volcengine.com/)
2. 进入 **对象存储 TOS** → 开通服务
3. 创建存储桶：
   - 桶名称：如 `openvikingdata`
   - 地域：如 `cn-beijing`（需与 VikingDB 等其他服务保持一致）
   - 存储类型：标准存储
   - 访问权限：私有
4. 记录桶名称、地域和 S3 兼容 endpoint，填入配置文件的 `storage.agfs.s3` 部分

> **注意**：AGFS 使用 S3 兼容协议访问 TOS，endpoint 需要使用 S3 兼容域名（带 `tos-s3-` 前缀），而非 TOS 控制台显示的标准域名。不同地域的 endpoint 不同，请查阅 [TOS 地域和访问域名文档](https://www.volcengine.com/docs/6349/107356) 获取你所在地域的 S3 兼容 endpoint。例如：
>
> | 地域 | S3 兼容 endpoint |
> |------|-----------------|
> | cn-beijing | `https://tos-s3-cn-beijing.volces.com` |
> | cn-shanghai | `https://tos-s3-cn-shanghai.volces.com` |
> | cn-guangzhou | `https://tos-s3-cn-guangzhou.volces.com` |

### 1.2 开通 VikingDB（向量数据库）

VikingDB 用于存储和检索向量嵌入。

1. 登陆 [火山引擎控制台](https://console.volcengine.com/) →  [进入 VikingDB 下单开通界面](https://console.volcengine.com/vikingdb/region:vikingdb+cn-beijing/home) -> 选择对应的地域并开通向量数据库
2. 开通服务（按量付费即可），选择与 TOS 相同的地域
3. 无需手动创建 Collection，OpenViking 启动后会自动创建
4. 在配置文件中填写 `storage.vectordb.volcengine.region`，OpenViking 会自动路由到对应地域的 VikingDB 服务

### 1.3 申请 AK/SK（IAM 访问密钥）

AK/SK 同时用于 TOS 和 VikingDB 的鉴权。

1. 进入 [火山引擎控制台](https://console.volcengine.com/) → **访问控制 IAM**
2. 创建子用户（建议不使用主账号 AK/SK）
3. 为子用户授权以下策略：
   - `TOSFullAccess`（或精确到桶级别的自定义策略）
   - `VikingDBFullAccess`
4. 为子用户创建 **AccessKey**，记录：
   - `Access Key ID`（即 AK）
   - `Secret Access Key`（即 SK）
5. 将 AK/SK 填入配置文件中的以下位置：
   - `storage.vectordb.volcengine.ak` / `sk`
   - `storage.agfs.s3.access_key` / `secret_key`

### 1.4 申请方舟 API Key

方舟平台提供 Embedding 和 VLM 模型的推理服务。

1. 进入 [火山方舟控制台](https://console.volcengine.com/ark)
2. 左侧菜单 → **API Key 管理** → 创建 API Key
3. 记录生成的 API Key
4. 确认以下模型已开通（在 **模型广场** 中申请）：
   - `doubao-embedding-vision-250615`（多模态 Embedding）
   - `doubao-seed-1-8-251228`（VLM 推理）
5. 将 API Key 填入配置文件的 `embedding.dense.api_key` 和 `vlm.api_key`

---

## 2. 准备配置文件

### 2.1 复制示例配置

```bash
cp examples/cloud/ov.conf.example examples/cloud/ov.conf
```

### 2.2 编辑配置

打开 `examples/cloud/ov.conf`，将占位符替换为真实值。需要替换的字段如下：

| 占位符 | 替换为 | 说明 |
|--------|--------|------|
| `<your-root-api-key>` | 自定义强密码 | 管理员密钥，用于多租户管理 |
| `<your-volcengine-ak>` | IAM Access Key ID | 火山引擎 AK，用于 TOS / VikingDB |
| `<your-volcengine-sk>` | IAM Secret Access Key | 火山引擎 SK |
| `<your-tos-bucket>` | TOS 桶名称 | 如 `openvikingdata` |
| `<your-ark-api-key>` | 方舟 API Key | 用于 Embedding 和 VLM |

此外，还需根据实际地域修改以下字段（示例中默认为 `cn-beijing`）：

| 字段 | 说明 |
|------|------|
| `storage.vectordb.volcengine.region` | VikingDB 地域，如 `cn-beijing`、`cn-shanghai`、`cn-guangzhou` |
| `storage.agfs.s3.region` | TOS 地域，需与桶所在地域一致 |
| `storage.agfs.s3.endpoint` | TOS 的 S3 兼容 endpoint，需与地域匹配（参考第 1.1 节） |

替换后的配置示例（脱敏）：

```json
{
  "server": {
    "root_api_key": "my-strong-secret-key-2024"
  },
  "storage": {
    "vectordb": {
      "volcengine": {
        "region": "cn-beijing",
        "ak": "AKLTxxxxxxxxxxxx",
        "sk": "T1dYxxxxxxxxxxxx"
      }
    },
    "agfs": {
      "s3": {
        "bucket": "openvikingdata",
        "region": "cn-beijing",
        "access_key": "AKLTxxxxxxxxxxxx",
        "secret_key": "T1dYxxxxxxxxxxxx",
        "endpoint": "https://tos-s3-cn-beijing.volces.com"
      }
    }
  },
  "embedding": {
    "dense": {
      "api_key": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
    }
  },
  "vlm": {
    "api_key": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
  }
}
```

> **注意**：`ov.conf` 已被 `.gitignore` 排除，不会被提交到版本库。请妥善保管你的凭据。

---

## 3. 启动服务

| 方式 | 需要容器运行时 | 适合节点数 | 环境隔离 | 弹性伸缩 | 典型场景 |
|------|:---:|:---:|:---:|:---:|------|
| **Docker（推荐）** | 是 | 单机 | 容器隔离 | 不支持 | 开发、测试、单机生产，最省事 |
| systemd | 否 | 单机 | 无 | 不支持 | VM 上不想装 Docker |
| Kubernetes + Helm | 是 | 暂不支持多节点 | 容器 + 编排 | 支持 | 已有 K8s 集群的团队 |

> **开发调试**：如果只是本地快速验证，可以直接运行：
> ```bash
> pip install openviking
>
> # 方式 A：放到默认路径
> mkdir -p ~/.openviking && cp examples/cloud/ov.conf ~/.openviking/ov.conf
> openviking-server
>
> # 方式 B：通过环境变量指定
> OPENVIKING_CONFIG_FILE=examples/cloud/ov.conf openviking-server
> ```

### 方式一：systemd

适合在 VM 上以系统服务方式长期运行。

1. 安装 OpenViking：

```bash
pip install openviking
```

2. 将配置文件放到固定路径：

```bash
sudo mkdir -p /etc/openviking
sudo cp ~/.openviking/ov.conf /etc/openviking/ov.conf
sudo chmod 600 /etc/openviking/ov.conf
```

3. 创建 systemd service 文件：

```bash
sudo tee /etc/systemd/system/openviking.service > /dev/null << 'EOF'
[Unit]
Description=OpenViking Server
After=network.target

[Service]
Type=simple
Environment=OPENVIKING_CONFIG_FILE=/etc/openviking/ov.conf
ExecStart=/usr/local/bin/openviking-server  # 替换为 which openviking-server 的实际输出
Restart=on-failure
RestartSec=5s
StandardOutput=journal
StandardError=journal
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
```

4. 启动服务：

```bash
sudo systemctl daemon-reload
sudo systemctl start openviking
sudo systemctl status openviking
```

5. 确认服务正常后，设置开机自启（可选）：

```bash
sudo systemctl enable openviking
```

常用管理命令：

```bash
sudo systemctl stop openviking       # 停止服务
sudo systemctl restart openviking    # 重启服务
journalctl -u openviking -f          # 查看实时日志
```

### 方式二：Docker

单容器场景用 `docker run` 或 `docker compose` 均可，效果相同。

**docker run：**

```bash
# 假设你的配置文件在 ~/.openviking/ov.conf
#
# -p  端口映射，宿主机端口:容器端口，启动后通过 localhost:1933 访问
# -v  挂载宿主机文件到容器内，格式为 宿主机路径:容器内路径
#     ov.conf 挂载是必填的，data 目录用于持久化数据（容器删除后不丢失）
# --restart  进程崩溃或机器重启后自动拉起

docker run -d \
  --name openviking \
  -p 1933:1933 \
  -v ~/.openviking/ov.conf:/app/ov.conf \
  -v /var/lib/openviking/data:/app/data \
  --restart unless-stopped \
  ghcr.io/volcengine/openviking:main
```

> 将 `~/.openviking/ov.conf` 替换为你实际的配置文件路径。

常用管理命令：

```bash
docker logs openviking        # 查看日志
docker logs -f openviking     # 实时跟踪日志
docker stop openviking        # 停止服务
docker restart openviking     # 重启服务
docker rm -f openviking       # 删除容器（重新 docker run 前需要先删除）
```

**docker compose：**

项目根目录的 `docker-compose.yml` 默认从 `/var/lib/openviking/ov.conf` 读取配置：

```bash
# 把你的配置文件复制到 docker-compose.yml 期望的路径
sudo mkdir -p /var/lib/openviking
sudo cp ~/.openviking/ov.conf /var/lib/openviking/ov.conf

# 在项目根目录下启动（-d 表示后台运行）
docker compose up -d
```

> 如果配置文件不在 `/var/lib/openviking/ov.conf`，需要修改 `docker-compose.yml` 中 `volumes` 的挂载路径。

常用管理命令：

```bash
docker compose stop        # 停止服务
docker compose restart     # 重启服务
docker compose logs -f     # 查看实时日志
```

> 如需自行构建镜像：`docker build -t openviking:latest .`

### 方式三：Kubernetes + Helm

Helm chart 默认的 `values.yaml` 只包含 embedding 和 vlm 配置。云上部署需要补充 storage、server 等字段。

推荐创建自定义 values 文件 `my-values.yaml`：

```yaml
openviking:
  config:
    server:
      root_api_key: "my-strong-secret-key-2024"
    storage:
      workspace: /app/data
      vectordb:
        name: context
        backend: volcengine
        project: default
        volcengine:
          region: cn-beijing
          ak: "AKLTxxxxxxxxxxxx"
          sk: "T1dYxxxxxxxxxxxx"
      agfs:
        port: 1833
        log_level: warn
        backend: s3
        timeout: 10
        retry_times: 3
        s3:
          bucket: "openvikingdata"
          region: cn-beijing
          access_key: "AKLTxxxxxxxxxxxx"
          secret_key: "T1dYxxxxxxxxxxxx"
          endpoint: "https://tos-s3-cn-beijing.volces.com"
          prefix: openviking
          use_ssl: true
          use_path_style: false
    embedding:
      dense:
        model: "doubao-embedding-vision-250615"
        api_key: "your-ark-api-key"
        api_base: "https://ark.cn-beijing.volces.com/api/v3"
        dimension: 1024
        provider: volcengine
        input: multimodal
    vlm:
      model: "doubao-seed-1-8-251228"
      api_key: "your-ark-api-key"
      api_base: "https://ark.cn-beijing.volces.com/api/v3"
      temperature: 0.0
      max_retries: 3
      provider: volcengine
      thinking: false
    auto_generate_l0: true
    auto_generate_l1: true
    default_search_mode: thinking
    default_search_limit: 3
    enable_memory_decay: true
    memory_decay_check_interval: 3600
```

然后安装：

```bash
helm install openviking ./examples/k8s-helm -f my-values.yaml
```

或者通过 `--set` 逐个传参（适合 CI/CD）：

```bash
helm install openviking ./examples/k8s-helm \
  --set openviking.config.server.root_api_key="my-strong-secret-key-2024" \
  --set openviking.config.embedding.dense.api_key="YOUR_ARK_API_KEY" \
  --set openviking.config.vlm.api_key="YOUR_ARK_API_KEY" \
  --set openviking.config.storage.vectordb.backend="volcengine" \
  --set openviking.config.storage.vectordb.volcengine.ak="YOUR_AK" \
  --set openviking.config.storage.vectordb.volcengine.sk="YOUR_SK" \
  --set openviking.config.storage.agfs.backend="s3" \
  --set openviking.config.storage.agfs.s3.bucket="openvikingdata" \
  --set openviking.config.storage.agfs.s3.access_key="YOUR_AK" \
  --set openviking.config.storage.agfs.s3.secret_key="YOUR_SK" \
  --set openviking.config.storage.agfs.s3.endpoint="https://tos-s3-cn-beijing.volces.com"
```

---

## 4. 验证

### 4.1 健康检查

```bash
curl http://localhost:1933/health
# 期望返回: {"status":"ok"}
```

### 4.2 就绪检查

就绪接口会检测 AGFS（TOS）和 VikingDB 的连接状态，是验证凭据是否正确的关键步骤：

```bash
curl http://localhost:1933/ready
# 期望返回: {"status":"ready","checks":{"agfs":"ok","vectordb":"ok","api_key_manager":"ok"}}
```

如果某个组件报错，请检查：

| checks 字段 | 失败原因 | 排查方向 |
|-------------|---------|---------|
| `agfs` | TOS 连接失败 | 检查 bucket、endpoint、AK/SK 是否正确 |
| `vectordb` | VikingDB 连接失败 | 检查 region、AK/SK、服务是否已开通 |
| `api_key_manager` | root_api_key 未配置 | 检查 `server.root_api_key` 字段 |

---

## 5. 多租户管理

OpenViking 支持多租户隔离。配置了 `root_api_key` 后自动启用多租户模式。

### 5.1 创建租户（Account）

使用 `root_api_key` 创建租户，同时会生成一个管理员用户：

```bash
curl -X POST http://localhost:1933/api/v1/admin/accounts \
  -H "X-API-Key: YOUR_ROOT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "account_id": "my-team",
    "admin_user_id": "admin"
  }'
```

返回结果中包含管理员的 API Key，**请妥善保存**：

```json
{
  "status": "ok",
  "result": {
    "account_id": "my-team",
    "admin_user_id": "admin",
    "user_key": "abcdef1234567890..."
  }
}
```

### 5.2 注册普通用户

租户管理员可以为租户添加用户：

```bash
curl -X POST http://localhost:1933/api/v1/admin/accounts/my-team/users \
  -H "X-API-Key: ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "alice",
    "role": "user"
  }'
```

返回用户的 API Key：

```json
{
  "status": "ok",
  "result": {
    "user_id": "alice",
    "user_key": "fedcba0987654321..."
  }
}
```

### 5.3 查看租户下的用户

```bash
curl http://localhost:1933/api/v1/admin/accounts/my-team/users \
  -H "X-API-Key: ADMIN_API_KEY"
```

---

## 6. 运行示例

`examples/cloud/` 目录下提供了完整的多租户 demo 脚本，演示从用户创建到数据使用的全流程。

### 6.1 setup_users.py — 初始化租户和用户

创建租户 `demo-team`，注册 alice（管理员）和 bob（普通用户），并将 API Key 写入 `user_keys.json` 供后续脚本使用。

```bash
# 确保 server 已启动，且 root_api_key 与 ov.conf 一致
uv run examples/cloud/setup_users.py --url http://localhost:1933 --root-key <your-root-api-key>
```

### 6.2 alice.py — 技术负责人的使用流程

Alice 演示：添加项目文档 → 语义搜索 → 多轮对话 → 沉淀记忆 → 回顾记忆。

```bash
uv run examples/cloud/alice.py
```

脚本会自动从 `user_keys.json` 读取 API Key。也可以手动指定：

```bash
uv run examples/cloud/alice.py --url http://localhost:1933 --api-key <alice_key>
```

### 6.3 bob.py — 新入职成员的使用流程

Bob 演示：浏览团队资源 → 回顾团队记忆（Alice 沉淀的决策） → 添加自己的资源 → 对话 → 沉淀记忆 → 带上下文搜索。

建议在 alice.py 执行完毕后运行，这样 Bob 可以看到 Alice 沉淀的团队记忆：

```bash
uv run examples/cloud/bob.py
```

### 完整流程汇总

```bash
# 1. 启动服务（确保 ~/.openviking/ov.conf 已就位）
openviking-server &

# 2. 等待服务就绪
curl http://localhost:1933/ready

# 3. 创建用户
uv run examples/cloud/setup_users.py --root-key <your-root-api-key>

# 4. Alice: 添加文档 + 对话 + 沉淀记忆
uv run examples/cloud/alice.py

# 5. Bob: 浏览团队资源和记忆 + 入职学习
uv run examples/cloud/bob.py
```

---

## 7. 运维

### 日志

容器日志默认输出到 stdout，可通过 `docker logs` 或 K8s 日志系统查看：

```bash
docker logs -f openviking
```

配置文件中 `log.level` 可调整日志级别（`DEBUG` / `INFO` / `WARN` / `ERROR`）。

### 监控

- 健康检查：`GET /health`
- 就绪检查：`GET /ready`（检测 AGFS、VikingDB、APIKeyManager 连接状态）
- 系统状态：`GET /api/v1/system/status`

### 数据备份

- **TOS 数据**：通过 TOS 控制台配置跨区域复制或定期备份
- **本地数据**（如使用 PVC）：定期快照 PersistentVolume

---

## 8. 常见问题

### systemd 启动失败（status=203/EXEC）

`status=203/EXEC` 表示 systemd 找不到 `ExecStart` 指定的可执行文件。常见于使用 venv / conda 环境安装 OpenViking 的情况，`openviking-server` 不在 `/usr/local/bin/` 下。

排查步骤：

```bash
# 1. 查找实际路径
which openviking-server

# 2. 将输出路径替换到 service 文件的 ExecStart
sudo sed -i 's|ExecStart=.*|ExecStart=/实际/路径/openviking-server|' /etc/systemd/system/openviking.service

# 3. 重新加载并启动
sudo systemctl daemon-reload
sudo systemctl restart openviking
sudo systemctl status openviking
```

### docker: command not found

系统未安装 Docker，请参考 [Docker 官方安装文档](https://docs.docker.com/engine/install/) 选择对应系统的安装方式。安装完成后启动 Docker：

```bash
sudo systemctl start docker
```

然后重新运行 `docker run` 命令即可。

### TOS 连接失败（agfs check failed）

- **endpoint 错误**：确认使用 S3 兼容 endpoint（带 `tos-s3-` 前缀），不要用标准 endpoint（`tos-cn-` 前缀）
- **地域不匹配**：确认 `storage.agfs.s3.region` 和 `storage.agfs.s3.endpoint` 与桶所在地域一致
- **bucket 不存在**：确认 TOS 控制台中桶已创建，且名称和地域与配置一致
- **AK/SK 无权限**：确认 IAM 子用户拥有 `TOSFullAccess` 或对应桶的访问策略

### VikingDB 鉴权失败（vectordb check failed）

- **服务未开通**：在火山引擎控制台确认 VikingDB 已开通
- **地域错误**：确认 `storage.vectordb.volcengine.region` 与开通服务的地域一致
- **AK/SK 错误**：确认 `storage.vectordb.volcengine.ak/sk` 与 IAM 密钥一致
- **权限不足**：确认 IAM 子用户拥有 `VikingDBFullAccess` 策略

### Embedding 模型调用失败

- **模型未开通**：在方舟控制台 **模型广场** 中确认 `doubao-embedding-vision-250615` 已申请并通过
- **API Key 错误**：确认 `embedding.dense.api_key` 填写正确
- **API Base 错误**：确认为 `https://ark.cn-beijing.volces.com/api/v3`

### helm: command not found

系统未安装 Helm，需要先安装：

```bash
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
```

安装后验证：

```bash
helm version
```

### Kubernetes cluster unreachable

```
Error: INSTALLATION FAILED: Kubernetes cluster unreachable: Get "http://localhost:8080/version": dial tcp [::1]:8080: connect: connection refused
```

服务器上没有运行 Kubernetes 集群。可以使用 k3s 快速搭建轻量级集群：

```bash
# 安装 k3s
curl -sfL https://get.k3s.io | sh -

# 配置 kubeconfig
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

# 永久生效
echo 'export KUBECONFIG=/etc/rancher/k3s/k3s.yaml' >> ~/.bashrc

# 验证集群就绪
kubectl get nodes
```

看到节点状态为 `Ready` 后，再执行 `helm install` 命令。

### helm install 时 path not found

```
Error: INSTALLATION FAILED: path "./examples/k8s-helm" not found
```

需要在 OpenViking 项目根目录下执行 `helm install` 命令：

```bash
cd /path/to/OpenViking
helm install openviking ./examples/k8s-helm -f my-values.yaml
```

### Helm 安装后 Pod CrashLoopBackOff

- 检查 `kubectl logs <pod-name>`，通常是配置字段缺失
- 确认 values 文件中包含完整的 storage、embedding、vlm 配置（参考第 3 节 Helm 部分）
- 确认 `openviking.config` 下的 JSON 结构正确（Helm 会将其序列化为 ov.conf）
