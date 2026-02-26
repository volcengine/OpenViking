# OpenViking SaaS 部署指南

> **架构升级说明**
> 本次改造将 OpenViking 的存储层从全本地化升级为 SaaS 数据库模式：
> - 向量索引 & 上下文元数据：**本地 C++ VectorDB** → **PostgreSQL + pgvector**
> - 文件内容存储：**本地文件系统** → **MinIO（S3 兼容对象存储）**

---

## 目录

1. [环境要求](#1-环境要求)
2. [架构说明](#2-架构说明)
3. [快速启动（Docker Compose）](#3-快速启动docker-compose)
4. [配置文件说明](#4-配置文件说明)
5. [验证部署](#5-验证部署)
6. [可视化面板使用指南](#6-可视化面板使用指南)
7. [REST API 参考](#7-rest-api-参考)
8. [本地开发模式（不使用 Docker）](#8-本地开发模式不使用-docker)
9. [生产环境注意事项](#9-生产环境注意事项)
10. [故障排查](#10-故障排查)

---

## 1. 环境要求

| 依赖 | 最低版本 | 说明 |
|------|----------|------|
| Docker | 20.10+ | 容器运行时 |
| Docker Compose | 2.0+ (`docker compose`) | 编排工具 |
| 可用内存 | 2 GB | PostgreSQL + MinIO + OpenViking |
| 可用磁盘 | 5 GB | 数据卷存储 |
| Embedding API | — | OpenAI 或兼容接口（必须有效的 API Key） |

---

## 2. 架构说明

```
┌─────────────────────────────────────────────────────┐
│                   OpenViking Server                  │
│               http://localhost:1933                  │
│                                                      │
│  ┌──────────────┐    ┌─────────────────────────────┐ │
│  │  AGFS (Go)   │    │     VikingFS / Service       │ │
│  │  文件系统抽象 │    │     上下文管理 / 搜索         │ │
│  └──────┬───────┘    └──────────┬──────────────────┘ │
│         │                       │                    │
└─────────┼───────────────────────┼────────────────────┘
          │ S3 API                │ asyncpg
          ▼                       ▼
┌─────────────────┐   ┌───────────────────────────────┐
│      MinIO      │   │    PostgreSQL 16 + pgvector    │
│  (对象存储)      │   │   (向量索引 + 上下文元数据)      │
│  :9000 (API)    │   │         :5432                  │
│  :9001 (控制台) │   │   表: ov_context               │
└─────────────────┘   └───────────────────────────────┘
```

**数据流向：**
- 上传文件/资源 → AGFS → 写入 MinIO 对象存储
- 生成向量嵌入 → 写入 PostgreSQL `ov_context` 表（带 pgvector）
- 语义搜索 → PostgreSQL 余弦相似度查询（`<=>` 算子）
- 元数据过滤 → PostgreSQL SQL WHERE 条件

---

## 3. 快速启动（Docker Compose）

### 第一步：克隆代码并进入项目目录

```bash
git clone <repo-url>
cd OpenViking
```

### 第二步：创建 SaaS 配置文件

```bash
cp ov.conf.saas.example ov.conf.saas
```

### 第三步：编辑配置文件，填入 Embedding API Key

```bash
# 用你喜欢的编辑器打开
nano ov.conf.saas
# 或
vi ov.conf.saas
```

**必须修改的字段：**

```json
{
  "embedding": {
    "dense": {
      "provider": "openai",
      "api_base": "https://api.openai.com/v1",
      "api_key": "sk-xxxxxxxxxxxxxxxx",   ← 替换为你的 OpenAI API Key
      "model": "text-embedding-3-small",
      "dimension": 1024
    }
  },
  "vlm": {
    "provider": "openai",
    "api_base": "https://api.openai.com/v1",
    "api_key": "sk-xxxxxxxxxxxxxxxx",     ← 同上
    "model": "gpt-4o"
  }
}
```

> **使用国内 API / 代理？** 修改 `api_base` 为你的代理地址，例如：
> ```json
> "api_base": "https://api.your-proxy.com/v1"
> ```

### 第四步：启动所有服务

```bash
docker compose -f docker-compose.saas.yml up -d
```

启动过程大约需要 30～60 秒，可以观察启动日志：

```bash
docker compose -f docker-compose.saas.yml logs -f
```

### 第五步：验证服务状态

```bash
docker compose -f docker-compose.saas.yml ps
```

期望输出（所有服务 `healthy` 或 `running`）：

```
NAME                     STATUS              PORTS
openviking-postgres      healthy             0.0.0.0:5432->5432/tcp
openviking-minio         healthy             0.0.0.0:9000-9001->9000-9001/tcp
openviking-minio-init    exited (0)          —
openviking-server        healthy             0.0.0.0:1933->1933/tcp
```

---

## 4. 配置文件说明

完整配置结构 `ov.conf.saas`：

```jsonc
{
  // ── 存储配置 ──────────────────────────────────────
  "storage": {
    "workspace": "/app/data",          // 容器内工作目录（勿修改）

    // AGFS 文件系统 → MinIO S3 后端
    "agfs": {
      "backend": "s3",
      "port": 1833,
      "s3": {
        "bucket": "openviking-storage", // MinIO bucket 名称
        "region": "us-east-1",          // 固定值，MinIO 不校验
        "access_key": "openviking",     // MinIO 用户名
        "secret_key": "openviking_secret", // MinIO 密码
        "endpoint": "http://minio:9000",   // 容器内地址（勿修改）
        "use_ssl": false,
        "use_path_style": true
      }
    },

    // 向量数据库 → PostgreSQL + pgvector
    "vectordb": {
      "backend": "postgresql",          // ← SaaS 模式的关键配置
      "name": "context",                // 集合名称（表名 ov_context）
      "dimension": 1024,                // 必须与 embedding.dimension 一致
      "postgresql": {
        "host": "postgres",             // 容器名（Docker 内部 DNS）
        "port": 5432,
        "database": "openviking",
        "user": "openviking",
        "password": "openviking_secret"
        // 也可用 DSN: "dsn": "postgresql://openviking:secret@postgres:5432/openviking"
      }
    }
  },

  // ── Embedding 模型配置 ────────────────────────────
  "embedding": {
    "dense": {
      "provider": "openai",             // 支持 openai / custom
      "api_base": "https://api.openai.com/v1",
      "api_key": "YOUR_OPENAI_API_KEY", // ← 必须修改
      "model": "text-embedding-3-small",
      "dimension": 1024                 // 必须与 vectordb.dimension 一致
    }
  },

  // ── VLM（视觉语言模型，用于图片/PDF 理解）──────────
  "vlm": {
    "provider": "openai",
    "api_base": "https://api.openai.com/v1",
    "api_key": "YOUR_OPENAI_API_KEY",   // ← 必须修改
    "model": "gpt-4o"
  },

  // ── HTTP 服务配置 ─────────────────────────────────
  "server": {
    "host": "0.0.0.0",
    "port": 1933,
    "cors_origins": ["*"]
  },

  // ── 多租户标识 ────────────────────────────────────
  "default_account": "default",
  "default_user": "default",
  "default_agent": "default",

  // ── 日志级别 ──────────────────────────────────────
  "log": { "level": "INFO" }
}
```

---

## 5. 验证部署

### 方式一：健康检查接口

```bash
curl http://localhost:1933/health
# 期望返回：{"status":"ok"}
```

### 方式二：系统状态接口

```bash
curl http://localhost:1933/api/v1/system/status
# 期望返回：{"status":"ok","result":{"initialized":true,...}}
```

### 方式三：存储后端统计

```bash
curl http://localhost:1933/api/v1/debug/storage/stats
# 期望返回包含 "backend": "postgresql"
```

示例返回：
```json
{
  "status": "ok",
  "result": {
    "backend": "postgresql",
    "collections": 1,
    "total_records": 0,
    "storage_size": 8388608
  }
}
```

### 方式四：访问 MinIO 控制台

浏览器打开：`http://localhost:9001`

- 用户名：`openviking`
- 密码：`openviking_secret`

确认 `openviking-storage` bucket 已创建。

---

## 6. 可视化面板使用指南

### 打开面板

浏览器访问：**`http://localhost:1933/dashboard`**

### 面板功能说明

#### Overview 标签（总览）

![overview](docs/images/dashboard-overview.png)

- **后端类型徽章**：页面右上角显示 `POSTGRESQL`（SaaS 模式）
- **统计卡片**：后端类型 / 集合数 / 总上下文数 / 存储大小
- **最近上下文**：列出最新写入的上下文记录（URI、类型、摘要）
- **快速操作**：刷新统计 / 跳转添加资源 / 跳转搜索

#### Context Browser 标签（上下文浏览器）

1. 在输入框中输入 URI，例如：`viking://resources/`
2. 点击 **List** 按钮，列出该目录下的文件和子目录
3. 点击目录的 **Open** 按钮可以进入子目录
4. 点击文件的 **View** 按钮查看文件内容和摘要

常用 URI 路径：
| URI | 说明 |
|-----|------|
| `viking://` | 根目录 |
| `viking://resources/` | 资源根目录 |
| `viking://agent/memories/` | 记忆存储 |
| `viking://agent/skills/` | 技能存储 |

#### Search 标签（语义搜索）

1. 在查询框中输入自然语言查询
2. （可选）在 Target URI 框中限定搜索范围，例如 `viking://resources/`
3. 调整返回数量（默认 10）
4. 点击 **Search**，结果按相似度评分排序显示
5. 点击结果的 **View** 按钮跳转到浏览器查看详情

#### Add Resource 标签（添加资源）

**添加外部资源/URL：**
1. 在 Resource Path / URL 框中输入路径
2. （可选）设置目标 URI（默认 `viking://resources/`）
3. 点击 **Add Resource**，系统自动解析并向量化

**添加文本内容：**
1. 在 URI 框中输入写入位置，例如 `viking://resources/my-notes.md`
2. 在 Content 框中输入文本内容
3. 点击 **Write to Storage**

#### Sessions 标签（会话管理）

- 列出所有会话，显示消息数和创建时间
- 点击 **View** 查看会话的完整消息历史

#### Debug 标签（API 控制台）

可以直接调用任意 REST API 端点：

1. 选择 HTTP 方法（GET / POST）
2. 输入端点路径，例如 `/api/v1/system/status`
3. POST 请求可以在 Body 框输入 JSON 请求体
4. 点击 **Send Request** 查看完整响应

---

## 7. REST API 参考

服务器地址：`http://localhost:1933`

### 系统

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| GET | `/api/v1/system/status` | 系统状态 |
| GET | `/api/v1/debug/storage/stats` | 存储后端统计 |
| GET | `/api/v1/debug/storage/list` | 列出上下文记录 |

### 资源管理

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/resources/add` | 添加资源（URL/文件） |
| DELETE | `/api/v1/resources/remove` | 删除资源 |
| GET | `/api/v1/resources/list` | 列出资源 |

**添加资源示例：**
```bash
curl -X POST http://localhost:1933/api/v1/resources/add \
  -H "Content-Type: application/json" \
  -d '{
    "path": "https://en.wikipedia.org/wiki/Artificial_intelligence",
    "target_uri": "viking://resources/"
  }'
```

### 文件系统

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/fs/ls` | 列出目录内容 |
| POST | `/api/v1/fs/read` | 读取文件 |
| POST | `/api/v1/fs/write` | 写入文件 |
| POST | `/api/v1/fs/mkdir` | 创建目录 |
| DELETE | `/api/v1/fs/rm` | 删除文件/目录 |

**列出目录示例：**
```bash
curl -X POST http://localhost:1933/api/v1/fs/ls \
  -H "Content-Type: application/json" \
  -d '{"uri": "viking://resources/", "recursive": false}'
```

### 语义搜索

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/search/find` | 语义搜索（无 session） |
| POST | `/api/v1/search/search` | 语义搜索（带 session 上下文） |

**搜索示例：**
```bash
curl -X POST http://localhost:1933/api/v1/search/find \
  -H "Content-Type: application/json" \
  -d '{
    "query": "机器学习算法",
    "target_uri": "viking://resources/",
    "limit": 5
  }'
```

### 内容读取

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/content/abstract?uri=...` | 获取摘要（L0） |
| GET | `/api/v1/content/overview?uri=...` | 获取概览（L1） |
| GET | `/api/v1/content/read?uri=...` | 读取原始内容 |

### 会话管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/sessions/list` | 列出会话 |
| POST | `/api/v1/sessions/create` | 创建会话 |
| GET | `/api/v1/sessions/messages?session_id=...` | 获取会话消息 |
| POST | `/api/v1/sessions/add_message` | 添加消息 |

**完整 API 文档（Swagger UI）：** `http://localhost:1933/docs`

---

## 8. 本地开发模式（不使用 Docker）

如果你希望在本地直接运行（不使用 Docker），需要手动准备依赖服务。

### 前置条件

**安装 PostgreSQL 16 + pgvector：**
```bash
# macOS
brew install postgresql@16
brew install pgvector  # 或从源码编译

# Ubuntu/Debian
sudo apt install postgresql-16 postgresql-16-pgvector
```

**创建数据库：**
```bash
psql -U postgres <<EOF
CREATE USER openviking WITH PASSWORD 'openviking_secret';
CREATE DATABASE openviking OWNER openviking;
\c openviking
CREATE EXTENSION vector;
EOF
```

**安装 MinIO（可选，用于文件存储）：**
```bash
# macOS
brew install minio/stable/minio
minio server ~/minio-data --console-address ":9001"

# Linux
wget https://dl.min.io/server/minio/release/linux-amd64/minio
chmod +x minio
./minio server ~/minio-data --console-address ":9001"
```

### 安装 OpenViking

```bash
# 安装 Python 依赖（包含新增的 asyncpg）
pip install -e ".[server]"
# 或使用 uv
uv sync
```

### 创建本地 SaaS 配置

```bash
cp ov.conf.saas.example ~/.openviking/ov.conf
# 编辑 ov.conf，将 postgres host 改为 localhost，minio endpoint 改为 http://localhost:9000
```

关键修改：
```json
{
  "storage": {
    "agfs": {
      "s3": {
        "endpoint": "http://localhost:9000"  ← 改为 localhost
      }
    },
    "vectordb": {
      "postgresql": {
        "host": "localhost"                  ← 改为 localhost
      }
    }
  }
}
```

### 启动服务器

```bash
openviking-server --config ~/.openviking/ov.conf --port 1933
```

或使用 Python：
```bash
python -m openviking.server.bootstrap --config ~/.openviking/ov.conf
```

---

## 9. 生产环境注意事项

### 安全加固

**修改默认密码：**
```bash
# docker-compose.saas.yml 中修改以下环境变量
POSTGRES_PASSWORD: <强密码>
MINIO_ROOT_PASSWORD: <强密码>

# ov.conf.saas 中同步修改
"password": "<强密码>"
"secret_key": "<强密码>"
```

**开启 API Key 认证：**
在 `ov.conf.saas` 中添加：
```json
{
  "server": {
    "root_api_key": "your-secret-api-key"
  }
}
```

之后所有 API 请求需要携带 Header：
```
Authorization: Bearer your-secret-api-key
```

**限制 CORS：**
```json
{
  "server": {
    "cors_origins": ["https://your-domain.com"]
  }
}
```

### 数据持久化

Docker Compose 默认使用命名 volume，数据不随容器删除而丢失：
- `postgres_data` → PostgreSQL 数据
- `minio_data` → MinIO 对象存储数据

**备份 PostgreSQL：**
```bash
docker exec openviking-postgres pg_dump -U openviking openviking > backup.sql
```

**恢复 PostgreSQL：**
```bash
cat backup.sql | docker exec -i openviking-postgres psql -U openviking openviking
```

### 替换为真实 S3

生产环境建议使用 AWS S3 或阿里云 OSS 替代 MinIO：

```json
{
  "storage": {
    "agfs": {
      "backend": "s3",
      "s3": {
        "bucket": "your-production-bucket",
        "region": "cn-hangzhou",
        "access_key": "AKIA...",
        "secret_key": "...",
        "endpoint": "https://oss-cn-hangzhou.aliyuncs.com",
        "use_ssl": true,
        "use_path_style": false
      }
    }
  }
}
```

---

## 10. 故障排查

### 问题：openviking-server 启动失败

**查看日志：**
```bash
docker compose -f docker-compose.saas.yml logs openviking
```

**常见原因：**

| 错误信息 | 原因 | 解决方法 |
|----------|------|----------|
| `connection refused` to postgres | PostgreSQL 未就绪 | 等待 10s 后重试，或检查 postgres 容器 |
| `FileNotFoundError: ov.conf` | 配置文件未挂载 | 确认 `ov.conf.saas` 文件存在于当前目录 |
| `invalid api key` | Embedding API Key 错误 | 检查 `ov.conf.saas` 中的 `api_key` |
| `asyncpg not found` | 依赖未安装 | `pip install asyncpg` |

### 问题：向量搜索无结果

1. 确认 Embedding API 可访问：
   ```bash
   curl -X POST http://localhost:1933/api/v1/search/find \
     -H "Content-Type: application/json" \
     -d '{"query": "test"}'
   ```

2. 查看嵌入队列是否有积压：
   ```bash
   curl http://localhost:1933/api/v1/debug/health
   ```

3. 检查 `ov_context` 表中是否有带向量的记录：
   ```bash
   docker exec openviking-postgres psql -U openviking openviking \
     -c "SELECT id, uri, (vector IS NOT NULL) as has_vector FROM ov_context LIMIT 10;"
   ```

### 问题：MinIO 连接失败

```bash
# 检查 MinIO 是否健康
docker compose -f docker-compose.saas.yml ps minio

# 手动测试 bucket 连接
docker exec openviking-minio mc ls local/openviking-storage
```

### 停止/重启服务

```bash
# 停止服务（保留数据）
docker compose -f docker-compose.saas.yml stop

# 重启服务
docker compose -f docker-compose.saas.yml restart

# 彻底清除（删除所有数据！）
docker compose -f docker-compose.saas.yml down -v
```

### 查看 PostgreSQL 中的数据

```bash
# 进入 PostgreSQL 命令行
docker exec -it openviking-postgres psql -U openviking openviking

# 常用查询
\dt                                    -- 列出所有表
SELECT COUNT(*) FROM ov_context;       -- 总记录数
SELECT id, uri, context_type, level, abstract FROM ov_context LIMIT 5;
SELECT uri, 1-(vector<=>vector) AS self_sim FROM ov_context WHERE vector IS NOT NULL LIMIT 3;
\q                                     -- 退出
```

---

## 相关链接

- **可视化面板**：`http://localhost:1933/dashboard`
- **Swagger API 文档**：`http://localhost:1933/docs`
- **MinIO 控制台**：`http://localhost:9001`（用户名/密码：`openviking` / `openviking_secret`）
- **项目主页**：[GitHub](https://github.com/volcengine/OpenViking)
