# OpenViking 上云部署指南

本文档介绍如何将 OpenViking 部署到火山引擎云上，使用 TOS（对象存储）+ VikingDB（向量数据库）+ 方舟大模型作为后端。

---

## 1. 开通云服务

### 1.1 开通 TOS（对象存储）

TOS 用于持久化存储 OpenViking 的文件数据（AGFS 后端）。

1. 登录 [火山引擎控制台](https://console.volcengine.com/)
2. 进入 **对象存储 TOS** → 开通服务
3. 创建存储桶：
   - 桶名称：如 `openvikingdata`
   - 地域：`cn-beijing`（需与其他服务保持一致）
   - 存储类型：标准存储
   - 访问权限：私有
4. 记录桶名称和地域，填入配置文件的 `storage.agfs.s3` 部分

### 1.2 开通 VikingDB（向量数据库）

VikingDB 用于存储和检索向量嵌入。

1. 进入 [火山引擎控制台](https://console.volcengine.com/) → **智能数据**  → **向量数据库 VikingDB**
2. 开通服务（按量付费即可）
3. VikingDB 的 API Host 默认为：`api-vikingdb.vikingdb.cn-beijing.volces.com`
4. 无需手动创建 Collection，OpenViking 启动后会自动创建

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
   - `rerank.ak` / `sk`（如果使用 rerank）

### 1.4 申请方舟 API Key

方舟平台提供 Embedding 和 VLM 模型的推理服务。

1. 进入 [火山方舟控制台](https://console.volcengine.com/ark)
2. 左侧菜单 → **API Key 管理** → 创建 API Key
3. 记录生成的 API Key
4. 确认以下模型已开通（在 **模型广场** 中申请）：
   - `doubao-embedding-vision-250615`（多模态 Embedding）
   - `doubao-seed-1-8-251228`（VLM 推理）
   - `doubao-seed-rerank`（Rerank，可选）
5. 将 API Key 填入配置文件的 `embedding.dense.api_key` 和 `vlm.api_key`

---

## 2. 编写配置文件

参考本目录下的 [ov.conf](./ov.conf)，将上述步骤获取的凭据填入。

关键字段说明：

| 字段 | 说明 |
|------|------|
| `server.root_api_key` | 管理员密钥，用于多租户管理，设置一个强密码 |
| `storage.vectordb.backend` | 设置为 `volcengine` 使用云端 VikingDB |
| `storage.vectordb.volcengine.ak/sk` | IAM 的 AK/SK |
| `storage.agfs.backend` | 设置为 `s3` 使用 TOS 存储 |
| `storage.agfs.s3.bucket` | TOS 桶名称 |
| `storage.agfs.s3.endpoint` | TOS 端点，北京为 `https://tos-cn-beijing.volces.com` |
| `storage.agfs.s3.access_key/secret_key` | IAM 的 AK/SK |
| `embedding.dense.api_key` | 方舟 API Key |
| `vlm.api_key` | 方舟 API Key |

---

## 3. 启动服务

### 方式一：Docker（推荐）

```bash
# 构建镜像（如果不使用预构建镜像）
docker build -t openviking:latest .

# 启动
docker run -d \
  --name openviking \
  -p 1933:1933 \
  -v $(pwd)/examples/cloud/ov.conf:/app/ov.conf \
  -v /var/lib/openviking/data:/app/data \
  --restart unless-stopped \
  openviking:latest
```

### 方式二：Docker Compose

修改 `docker-compose.yml` 中的配置挂载路径后：

```bash
docker-compose up -d
```

### 方式三：Kubernetes + Helm

```bash
helm install openviking ./examples/k8s-helm \
  --set openviking.config.embedding.dense.api_key="YOUR_ARK_API_KEY" \
  --set openviking.config.vlm.api_key="YOUR_ARK_API_KEY" \
  --set openviking.config.storage.vectordb.volcengine.ak="YOUR_AK" \
  --set openviking.config.storage.vectordb.volcengine.sk="YOUR_SK"
```

### 方式四：直接运行

```bash
pip install openviking
export OPENVIKING_CONFIG_FILE=$(pwd)/examples/cloud/ov.conf
openviking-server
```

### 验证启动

```bash
# 健康检查
curl http://localhost:1933/health
# 期望返回: {"status":"ok"}

# 就绪检查（验证 AGFS、VikingDB 连接）
curl http://localhost:1933/ready
# 期望返回: {"status":"ready","checks":{"agfs":"ok","vectordb":"ok","api_key_manager":"ok"}}
```

---

## 4. 注册租户和用户

OpenViking 支持多租户隔离。配置了 `root_api_key` 后自动启用多租户模式。

### 4.1 创建租户（Account）

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

### 4.2 注册普通用户

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

### 4.3 查看租户下的用户

```bash
curl http://localhost:1933/api/v1/admin/accounts/my-team/users \
  -H "X-API-Key: ADMIN_API_KEY"
```

---

## 5. 使用

以下操作使用用户的 API Key 进行。

### 5.1 添加资源

```bash
# 添加一个 URL 资源
curl -X POST http://localhost:1933/api/v1/resources \
  -H "X-API-Key: USER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "path": "https://raw.githubusercontent.com/volcengine/OpenViking/main/README.md",
    "reason": "项目文档"
  }'

# 上传本地文件（先上传到临时路径，再添加为资源）
curl -X POST http://localhost:1933/api/v1/resources/temp_upload \
  -H "X-API-Key: USER_API_KEY" \
  -F "file=@./my-document.pdf"

# 然后使用返回的 temp_path 添加资源
curl -X POST http://localhost:1933/api/v1/resources \
  -H "X-API-Key: USER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "temp_path": "/tmp/upload_xyz",
    "reason": "内部文档"
  }'
```

### 5.2 等待处理完成

添加资源后，系统会异步进行解析和向量化。等待处理完成：

```bash
curl -X POST http://localhost:1933/api/v1/system/wait \
  -H "X-API-Key: USER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"timeout": 120}'
```

### 5.3 语义搜索

```bash
curl -X POST http://localhost:1933/api/v1/search/find \
  -H "X-API-Key: USER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "OpenViking 是什么",
    "limit": 5
  }'
```

### 5.4 浏览文件系统

```bash
# 列出根目录
curl "http://localhost:1933/api/v1/fs/ls?uri=viking://" \
  -H "X-API-Key: USER_API_KEY"

# 查看目录树
curl "http://localhost:1933/api/v1/fs/tree?uri=viking://&depth=2" \
  -H "X-API-Key: USER_API_KEY"
```

### 5.5 读取内容

```bash
# 读取文件内容
curl "http://localhost:1933/api/v1/content/read?uri=viking://resources/doc1" \
  -H "X-API-Key: USER_API_KEY"

# 读取摘要
curl "http://localhost:1933/api/v1/content/abstract?uri=viking://resources/doc1" \
  -H "X-API-Key: USER_API_KEY"
```

### 5.6 会话管理（Memory）

```bash
# 创建会话
curl -X POST http://localhost:1933/api/v1/sessions \
  -H "X-API-Key: USER_API_KEY"

# 添加对话消息
curl -X POST http://localhost:1933/api/v1/sessions/{session_id}/messages \
  -H "X-API-Key: USER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "role": "user",
    "content": "帮我分析这个文档的核心观点"
  }'

# 带会话上下文的搜索
curl -X POST http://localhost:1933/api/v1/search/search \
  -H "X-API-Key: USER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "核心观点",
    "session_id": "SESSION_ID",
    "limit": 5
  }'
```

### 5.7 Python SDK 使用

```python
import openviking as ov

client = ov.SyncHTTPClient(
    url="http://localhost:1933",
    api_key="USER_API_KEY",
    agent_id="my-agent",
    timeout=120.0,  # HTTP request timeout in seconds (default: 60.0)
)
client.initialize()

# 添加资源
client.add_resource(
    path="https://example.com/doc.pdf",
    reason="参考文档"
)
client.wait_processed(timeout=120)

# 搜索
results = client.find("OpenViking 架构设计", limit=5)
for r in results:
    print(r.uri, r.score)

client.close()
```

---

## 6. 运维

### 日志

容器日志默认输出到 stdout，可通过 `docker logs` 或 K8s 日志系统查看：

```bash
docker logs -f openviking
```

### 监控

- 健康检查：`GET /health`
- 就绪检查：`GET /ready`（检测 AGFS、VikingDB、APIKeyManager 连接状态）
- 系统状态：`GET /api/v1/system/status`

### 数据备份

- **TOS 数据**：通过 TOS 控制台配置跨区域复制或定期备份
- **本地数据**（如使用 PVC）：定期快照 PersistentVolume
