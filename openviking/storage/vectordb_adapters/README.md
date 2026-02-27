# VectorDB Adapter 接入指南（新增第三方后端）

本指南说明如何在 `openviking/storage/vectordb_adapters` 下新增一个第三方向量库后端，并接入 OpenViking 现有检索链路。

---

## 1. 目标与范围

### 目标
- 以最小改动新增一个向量库后端。
- 保持上层业务接口不变（`find/search` 等无需改调用方式）。
- 将后端差异封装在 Adapter 层，不泄漏到业务层。

### 非目标
- 不改上层语义检索策略（租户、目录层级、召回策略）。
- 不增加新的对外 API 协议。

---

## 2. 架构位置与职责

当前分层职责如下：

1. **上层语义层（OpenViking 业务）**  
   面向语义接口，不关心后端协议差异。

2. **通用向量存储层（Store/Backend）**  
   提供统一查询、写入、删除、计数能力。

3. **Adapter 层（本目录）**  
   负责把统一能力映射到具体后端实现（local/http/volcengine/vikingdb/thirdparty）。

新增后端时，主要只改第 3 层。

---

## 3. 接入前提

在开始前，请确认：

- 你已拿到第三方后端的：
  - 集合管理 API（查/建/删集合）
  - 数据 API（upsert/get/delete/search/aggregate）
- 你已明确该后端的：
  - 认证方式（AK/SK、token、header）
  - 过滤语法能力（是否支持 must/range/and/or）
  - 索引参数约束（dense/sparse、距离度量、索引类型）

---

## 4. 接入步骤

## Step 1：新增 Adapter 文件

在目录下新增文件，例如：

- `openviking/storage/vectordb_adapters/thirdparty_adapter.py`

定义类：

- `ThirdPartyCollectionAdapter(CollectionAdapter)`

基类位于：

- `openviking/storage/vectordb_adapters/base.py`

---

## Step 2：实现最小必需方法

你需要实现以下方法：

1. `from_config(cls, config)`  
   - 从 `VectorDBBackendConfig` 读取后端配置并构造 adapter。
   - collection 名建议使用 `config.name or "context"`。

2. `_load_existing_collection_if_needed(self)`  
   - 懒加载已存在 collection handle。
   - 若不存在，保持 `_collection is None`。

3. `_create_backend_collection(self, meta)`  
   - 按传入 schema 创建 collection 并返回 handle。

---

## Step 3：按后端能力补充可选 Hook

如后端有差异，可重写：

- `_sanitize_scalar_index_fields(...)`
- `_build_default_index_meta(...)`

目的：把后端特性差异收敛在 adapter 内。

---

## Step 4：注册到 Factory

编辑：

- `openviking/storage/vectordb_adapters/factory.py`

在 `_ADAPTER_REGISTRY` 增加映射，例如：

```python
"thirdparty": ThirdPartyCollectionAdapter
```

这样 `create_collection_adapter(config)` 会自动路由到你的实现。

---

## Step 5：补充配置模型

确保配置中可声明新 backend（如 `backend: thirdparty`）及其专属字段（endpoint/auth/region 等）。

原则：
- `create_collection` 时使用配置中的 name 绑定 collection。
- 后续操作默认绑定，不需要每次传 collection_name。

---

## 5. Filter 与查询兼容规则

- Adapter 需要兼容统一过滤表达。
- 上层传入的过滤表达会经由统一编译流程进入后端查询。
- 若第三方语法不同，请在 adapter 内做映射，不改上层调用协议。

关键原则：
- **后端 DSL 不上浮到业务层**。
- **业务层不依赖第三方私有查询语法**。

---

## 6. 最小代码骨架（示例）

```python
from __future__ import annotations
from typing import Any, Dict

from openviking.storage.vectordb_adapters.base import CollectionAdapter

class ThirdPartyCollectionAdapter(CollectionAdapter):
    def __init__(self, *, endpoint: str, token: str, collection_name: str):
        super().__init__(collection_name=collection_name)
        self.mode = "thirdparty"
        self._endpoint = endpoint
        self._token = token

    @classmethod
    def from_config(cls, config: Any):
        if not config.thirdparty or not config.thirdparty.endpoint:
            raise ValueError("ThirdParty backend requires endpoint")
        return cls(
            endpoint=config.thirdparty.endpoint,
            token=config.thirdparty.token,
            collection_name=config.name or "context",
        )

    def _load_existing_collection_if_needed(self) -> None:
        if self._collection is not None:
            return
        # TODO: 查询远端 collection 是否存在，存在则初始化 handle
        # self._collection = ...
        pass

    def _create_backend_collection(self, meta: Dict[str, Any]):
        # TODO: 调后端 create collection，并返回 collection handle
        # return ...
        raise NotImplementedError
```

---

## 7. 测试要求（必须）

至少覆盖以下场景：

1. backend 工厂路由正确（能创建到新 adapter）。
2. collection 生命周期可用（exists/create/drop）。
3. 基础数据链路可用（upsert/get/delete/query）。
4. count/aggregate 行为正确。
5. filter 条件可正确生效（含组合条件）。

---

## 8. 常见问题与排查

### Q1：启动时报 backend 不支持
- 检查 factory 是否注册。
- 检查配置里的 backend 字符串是否与 registry key 一致。

### Q2：集合创建成功但查询为空
- 检查 collection 绑定名是否一致。
- 检查索引是否创建成功。
- 检查 filter 映射是否把条件误转成空条件。

### Q3：count 与 query 条数不一致
- 检查 aggregate API 的字段命名与返回结构解析。
- 检查 count 使用的 filter 与 query 使用的 filter 是否一致。

---

## 9. 验收标准

当满足以下条件，即可视为接入完成：

- `backend=thirdparty` 可正常初始化。
- create 后可完成 upsert/get/query/delete/count 全流程。
- 不改上层业务调用方式即可参与 `find/search` 检索链路。
- 后端差异全部封装在 adapter 层。
