# OpenViking 与 OceanBase 集成指南

本文介绍如何将 [OceanBase](https://www.oceanbase.com/) 作为 OpenViking 的向量库后端，并**通过官方实战示例**从零跑通语义检索。

---

## 一、集成概述

- 通过 [pyobvector](https://github.com/oceanbase/pyobvector) 连接 OceanBase，使用其向量表与 HNSW 索引。
- 在配置中设置 `storage.vectordb.backend = "oceanbase"` 并填写 `oceanbase` 连接块即可切换后端。
- 适用于希望将上下文索引落在关系型/HTAP 数据库或复用现有 OceanBase 的场景。

**前置条件**：OceanBase 4.3.3.0+（支持向量类型与向量索引）；需安装 `pyobvector` 或 `openviking[oceanbase]`。

---

## 二、安装与配置

### 2.1 安装

```bash
# 方式一：仅安装 pyobvector（若已安装 openviking）
pip install pyobvector

# 方式二：安装 openviking 时一并安装 OceanBase 依赖（推荐）
pip install openviking[oceanbase]
```

### 2.2 配置示例（ov.conf）

在 `~/.openviking/ov.conf` 中增加或修改 `storage.vectordb`，并填写 `oceanbase` 连接信息：

```json
{
  "storage": {
    "vectordb": {
      "name": "context",
      "backend": "oceanbase",
      "distance_metric": "l2",
      "oceanbase": {
        "uri": "127.0.0.1:2881",
        "user": "root@test",
        "password": "",
        "db_name": "openviking"
      }
    }
  }
}
```

> **说明**：若使用 Docker 启动的 OceanBase（slim 模式），建议将 `distance_metric` 设为 `"l2"`；若使用支持 cosine 的 OceanBase 版本，可设为 `"cosine"`。完整配置项见 [配置说明](./01-configuration.md#vectordb)。

首次写入或启动服务时，OpenViking 会按 Context Schema 自动建表与索引，无需手动建表。

---

## 三、实战示例一：5 分钟跑通 OpenViking + OceanBase

本示例与 [快速开始](../getting-started/02-quickstart.md) 风格一致：从启动 OceanBase 到完成一次语义检索，全程可复现。

### 步骤 1：启动 OceanBase（Docker）

若本机尚未安装 OceanBase，可使用 Docker 快速启动单机实例（首次会拉取镜像，启动约需数分钟）：

```bash
# 启动容器（端口 2881）
docker run -d -p 2881:2881 --name oceanbase-ce -e MODE=slim oceanbase/oceanbase-ce

# 等待启动完成（日志出现 "boot success!"）
docker logs oceanbase-ce 2>&1 | tail -5

# 创建数据库（root@test 租户）
docker exec -it oceanbase-ce mysql -h127.0.0.1 -P2881 -uroot@test -e "CREATE DATABASE IF NOT EXISTS openviking;"
```

若使用已有 OceanBase，请确保已创建 `ov.conf` 中 `oceanbase.db_name` 对应的数据库，并保证网络与账号权限可用。

### 步骤 2：准备配置文件

确保 `~/.openviking/ov.conf` 中已配置 **embedding**（与 [快速开始](../getting-started/02-quickstart.md) 相同）和 **storage.vectordb（OceanBase）**。以下为最小示例（请将 embedding 的 api_key、model 等替换为实际值）：

```json
{
  "embedding": {
    "dense": {
      "api_base": "<your-embedding-endpoint>",
      "api_key": "<your-api-key>",
      "provider": "<volcengine|openai|...>",
      "dimension": 1024,
      "model": "<your-embedding-model>"
    }
  },
  "storage": {
    "vectordb": {
      "name": "context",
      "backend": "oceanbase",
      "distance_metric": "l2",
      "oceanbase": {
        "uri": "127.0.0.1:2881",
        "user": "root@test",
        "password": "",
        "db_name": "openviking"
      }
    }
  }
}
```

各模型服务的完整配置见 [配置指南 - 配置示例](./01-configuration.md#配置示例)。

### 步骤 3：创建示例脚本

创建 `example_oceanbase.py`，内容如下（与快速开始示例一致，仅向量库改为 OceanBase）：

```python
import openviking as ov

# 使用默认配置 ~/.openviking/ov.conf（其中 vectordb 已设为 oceanbase）
client = ov.OpenViking(path="./data")

try:
    client.initialize()

    # 添加资源（支持 URL、本地文件或目录）
    add_result = client.add_resource(
        path="https://raw.githubusercontent.com/volcengine/OpenViking/refs/heads/main/README.md"
    )
    root_uri = add_result["root_uri"]

    # 查看资源结构
    ls_result = client.ls(root_uri)
    print(f"目录结构:\n{ls_result}\n")

    # 等待语义处理完成（向量将写入 OceanBase）
    print("等待语义处理...")
    client.wait_processed()

    # 获取摘要与概览
    abstract = client.abstract(root_uri)
    overview = client.overview(root_uri)
    print(f"摘要:\n{abstract}\n\n概览:\n{overview}\n")

    # 语义检索（底层从 OceanBase 做向量搜索）
    results = client.find("what is openviking", target_uri=root_uri)
    print("检索结果:")
    for r in results.resources:
        print(f"  {r.uri}  (score: {r.score:.4f})")

    client.close()

except Exception as e:
    print(f"错误: {e}")
```

### 步骤 4：运行

```bash
python example_oceanbase.py
```

### 步骤 5：预期输出

```
目录结构:
...

等待语义处理...
摘要:
...

概览:
...

检索结果:
  viking://resources/... (score: 0.xxxx)
  ...
```

至此，你已用 OceanBase 作为向量库完成 OpenViking 的首次语义检索。内容仍存储在本地 AGFS（`path="./data"`），向量与元数据存储在 OceanBase 中。

---

## 四、实战示例二：企业知识库（批量导入 + 按范围检索）

本示例演示：将多个资源导入后，通过自然语言在指定 URI 范围内检索，适合企业知识库、Wiki 等场景。

### 步骤 1：准备内容与配置

- 确保 OceanBase 已启动并已创建数据库（同实战示例一）。
- 确保 `ov.conf` 中 `storage.vectordb.backend` 为 `oceanbase`，且 embedding、oceanbase 连接已配置。

### 步骤 2：批量导入并检索

创建 `example_knowledge_base.py`：

```python
import openviking as ov

client = ov.OpenViking(path="./data")
client.initialize()

# 批量添加资源（本地目录或 URL）
client.add_resource("/path/to/your/wiki")        # 本地目录
client.add_resource("https://example.com/doc.md") # 或 URL
client.wait_processed()

# 在指定 URI 前缀下做语义检索（多租户/多项目时可限定范围）
results = client.find(
    "用户登录与鉴权流程",
    target_uri="viking://resources/",
    limit=5
)

print(f"共 {results.total} 条相关结果")
for ctx in results.resources:
    print(f"  {ctx.uri}")
    print(f"    score={ctx.score:.3f}  abstract={ctx.abstract[:80]}...")
    print()

client.close()
```

将 `/path/to/your/wiki` 替换为实际文档目录或删除该行仅用 URL。运行：

```bash
python example_knowledge_base.py
```

通过 `target_uri="viking://resources/"` 可限定只在资源树下检索；不同业务可约定不同 URI 前缀（如 `viking://resources/tenant-a/`）实现逻辑隔离。

---

## 五、Docker 快速启动参考

| 步骤 | 命令 |
|------|------|
| 启动 OceanBase | `docker run -d -p 2881:2881 --name oceanbase-ce -e MODE=slim oceanbase/oceanbase-ce` |
| 等待就绪 | `docker logs oceanbase-ce 2>&1 \| tail -1` 出现 `boot success!` |
| 建库 | `docker exec -it oceanbase-ce mysql -h127.0.0.1 -P2881 -uroot@test -e "CREATE DATABASE IF NOT EXISTS openviking;"` |

配置中填写 `oceanbase.uri: "127.0.0.1:2881"`、`oceanbase.db_name: "openviking"` 即可。

---

## 六、距离度量与版本

| distance_metric | 说明 |
|-----------------|------|
| `cosine` | 在支持的 OceanBase 版本中会映射为 neg_ip |
| `l2` / `ip` | 直接对应 OceanBase 的 L2 / IP 距离 |

若报错「this type of vector index distance algorithm is not supported」，请将 `distance_metric` 改为 `"l2"` 后重试（Docker slim 模式建议使用 `l2`）。

---

## 七、运行集成测试

本仓库内 OceanBase 相关测试**默认通过 Docker 启动 OceanBase**，无需本机预先安装：

```bash
# 需已安装 Docker；会自动拉取并启动 oceanbase/oceanbase-ce
pytest tests/vectordb/test_oceanbase_live.py -v -s
# 或
python -m unittest tests.vectordb.test_oceanbase_live -v
```

---

## 八、相关文档

- [配置说明](./01-configuration.md) — `storage.vectordb` 与各后端通用参数
- [存储架构](../concepts/05-storage.md) — 向量库在 OpenViking 中的角色
- [快速开始](../getting-started/02-quickstart.md) — 5 分钟上手 OpenViking（默认本地向量库）
- [OceanBase 集成（英文）](../../en/guides/06-oceanbase-integration.md) — 英文版本文档
