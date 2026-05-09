# OpenViking 本地 Embedding Llama-cpp 设计文档

Date: 2026-04-11
Status: 已批准进入实现

## 目标

为 OpenViking 增加内置的本地 dense embedding 能力，并满足以下产品行为：

- 当用户没有显式配置 `embedding` 时，OpenViking 默认使用本地 embedding backend
- 默认本地模型为 `bge-small-zh-v1.5-f16`
- 本地推理基于 `llama-cpp-python` 加载 GGUF 模型
- 本地推理依赖不放入主依赖，而是通过 optional extra 单独分发，降低安装风险

最终效果是：在不改变“默认走本地 embedding”这一产品目标的前提下，让方案具备可实现性和可维护性。

## 范围

本次设计包含：

- 新增 `embedding.backend = "local"` backend
- 当用户未提供 embedding 配置时，自动生成隐式本地 embedding 配置
- 基于 `llama-cpp-python` 的 dense embedder
- 默认模型 `bge-small-zh-v1.5-f16`
- 模型路径解析、下载和缓存目录管理
- query/document 双路编码语义
- collection 元数据校验和 rebuild 规则
- 启动期校验与错误提示
- 测试方案和 benchmark 预留

本次设计不包含：

- 本地 sparse embedding
- 本地 hybrid embedding
- 本地失败后静默回退到远程 provider
- 运行时自动安装依赖
- 替换现有远程 provider

## 决策摘要

OpenViking 采用以下组合策略：

1. 产品默认行为：如果用户没有配置 embedding，OpenViking 会隐式选择本地 embedding backend。
2. 依赖分发策略：`llama-cpp-python` 不进入主依赖，而是通过 `openviking[local-embed]` 之类的 optional extra 分发。
3. 默认本地模型：`bge-small-zh-v1.5-f16`。
4. 失败策略：如果系统默认选择了本地 embedding，但本地依赖或模型不可用，则直接报错，并给出清晰恢复指引；不会静默回退到远程模型。

这和 QMD 的做法不完全相同。QMD 是 Node CLI 产品，可以把 `node-llama-cpp` 作为主依赖；而 OpenViking 是 Python SDK 和服务组件，如果让原生依赖阻断主包安装，代价会更高。

## 为什么这样设计

调研结论和当前代码约束基本指向同一个方向：

- QMD 证明了“默认本地 embedding”这个产品方向是成立的。
- OpenClaw / ArkClaw 证明了基于 GGUF 的本地 memory search 有明确用户价值。
- OpenViking 当前架构决定了 embedding 初始化失败会在启动期暴露，而不是延后到查询时。
- 在 Python 生态里，原生依赖失败的成本通常比 QMD 所在的 npm/Node 生态更高。

因此，这个设计是在保留产品目标的前提下，尽量缩小原生依赖失败的影响范围。

## 用户可见行为

### 默认行为

如果配置中没有 `embedding`：

- OpenViking 自动生成一份隐式 local dense embedding 配置
- backend 设置为 `local`
- model 设置为 `bge-small-zh-v1.5-f16`
- dimension 设置为该模型对应维度

用户应感知到的行为是：“本地 embedding 是默认值”。

### 显式行为

如果用户显式配置了 `embedding`，则始终以显式配置为准，包括：

- 显式 `backend: "local"`
- 显式远程 backend，例如 `openai`、`volcengine`、`vikingdb`
- 显式 `model_path`
- 显式 `cache_dir`

不应存在覆盖用户配置的隐式重写。

### 安装体验

基础安装：

```bash
pip install openviking
```

启用本地 embedding：

```bash
pip install "openviking[local-embed]"
```

如果用户依赖默认本地行为，但没有安装 local extra，系统必须在启动时给出可执行的错误提示，至少包含：

- 当前默认启用了本地 embedding
- 缺少 `llama-cpp-python`
- 启用本地 embedding 的安装命令
- 如果用户想改成远程 provider，应如何显式配置

## 配置设计

在 `EmbeddingModelConfig` 中新增 `local` 作为合法 backend。

本地 dense backend 支持的字段：

- `backend`: `"local"`
- `model`: 逻辑模型名，默认 `bge-small-zh-v1.5-f16`
- `model_path`: 可选，显式指定 GGUF 文件路径
- `cache_dir`: 可选，缓存根目录，默认 `~/.cache/openviking/models/`
- `dimension`: 可选，但通常应由内置模型注册表推导
- `batch_size`: 预留给后续批量 embedding

建议配置示例：

```json
{
  "embedding": {
    "dense": {
      "backend": "local",
      "model": "bge-small-zh-v1.5-f16",
      "cache_dir": "~/.cache/openviking/models"
    }
  }
}
```

显式模型路径示例：

```json
{
  "embedding": {
    "dense": {
      "backend": "local",
      "model": "bge-small-zh-v1.5-f16",
      "model_path": "/data/models/bge-small-zh-v1.5-f16.gguf"
    }
  }
}
```

## 架构设计

### 新增组件

新增一个本地 dense embedder 实现，例如：

- `openviking/models/embedder/local_embedders.py`
- `LocalDenseEmbedder`

其职责包括：

- 校验 `llama-cpp-python` 是否可用
- 将逻辑模型名解析为 GGUF 模型规格
- 解析或下载模型文件
- 初始化 llama embedding context
- 提供 query/document 双路 embedding 方法
- 返回模型维度
- 在 `close()` 时释放本地资源

### Factory 与配置改造

需要修改：

- `EmbeddingModelConfig.validate_config()` 以接受 `backend == "local"`
- `EmbeddingConfig._create_embedder()` 以支持 `("local", "dense")`
- 默认配置生成逻辑，使“缺失 embedding 配置”自动变成 local dense

### 模型注册表

新增一个内置本地模型注册表。第一版可以先做成简单映射，按逻辑模型名索引：

- 逻辑模型名
- GGUF 下载 URL 或 HuggingFace 定位信息
- 预期维度
- 推荐 prompt 规则
- 可选的目标文件名

首个内置模型为：

- `bge-small-zh-v1.5-f16`

## Query / Document 双路编码

这部分不是可选优化，而是本方案必须处理的设计点。

BGE/E5 一类模型是检索导向模型，通常需要区分：

- query：用户输入的搜索词或问题
- document：被存储和检索的文本块

OpenViking 当前只有 `embed(text)`，这不足以表达这种语义差异。

设计上新增显式接口：

- `embed_query(text: str) -> EmbedResult`
- `embed_document(text: str) -> EmbedResult`

为了兼容现有代码，`embed(text)` 可以保留为一个薄封装，但内部必须带角色语义。新的检索代码应调用 `embed_query()`，新的入库代码应调用 `embed_document()`。

query/document 的格式规则必须封装在本地 embedder 内部，而不是散落在业务层拼装。

## 模型解析与下载流程

### 解析顺序

1. 如果配置了 `model_path`，直接使用该路径。
2. 否则通过内置本地模型注册表解析 `model`。
3. 如果目标文件不存在，则下载到 `cache_dir`。
4. 用解析后的 GGUF 文件初始化 `llama-cpp-python`。

### 缓存目录

默认目录：

- `~/.cache/openviking/models/`

行为要求：

- 目录不存在时自动创建
- 下载后的 GGUF 文件保存在这里
- 如果目标文件已存在，则不重复下载

### 下载策略

第一版需要支持：

- 可读性好的错误输出
- 稳定可预测的文件命名
- 失败后可手动重试

第一版暂不要求：

- 断点续传
- 多镜像源自动切换
- 后台异步下载器

## 启动时机与失败行为

OpenViking 当前在 client 启动时就初始化 embedder，本地方案保持这一行为。

因此，下面这些问题都会在启动期直接暴露：

- 没有安装 local extra
- `llama-cpp-python` import 失败
- 模型文件缺失且下载失败
- GGUF 文件存在但加载失败
- 当前 collection 元数据与配置模型不一致

### 错误处理规则

缺少本地依赖：

- 直接抛出明确的配置/运行时错误
- 错误信息中必须包含：缺失包名、安装命令、切换远程 provider 的方法

模型下载失败：

- 抛出包含逻辑模型名、解析 URL、缓存目录和原始异常的错误

模型加载失败：

- 抛出 GGUF 不兼容、文件损坏或当前运行环境不支持的错误

元数据不一致：

- 抛出“当前 embedding 设置与已有索引不兼容，需要 rebuild”的错误

不允许静默回退：

- 本地初始化失败时，不得悄悄切换到 `openai`、`volcengine` 或 `vikingdb`

## Collection 元数据与重建规则

当前系统只在写入时校验向量维度，这在本地模型成为默认值之后是不够的。

需要至少持久化以下元数据：

- `embedding_backend`
- `embedding_model`
- `embedding_dimension`
- `embedding_model_identity`

其中 `embedding_model_identity` 用于区分“看起来模型名相同，但实际模型文件不同”的情况，可以采用：

- 解析后的模型路径
- 模型路径哈希
- 文件哈希（如果成本可接受）

### 重建触发条件

只要以下任一项发生变化：

- backend
- model
- dimension
- model identity

都应判定现有向量不可兼容。系统需要：

- 在启动时直接报错并提示 rebuild，或
- 在用户显式触发时执行 rebuild 流程

第一版建议采用显式 rebuild，而不是隐式迁移。

## 数据流改造

### 入库流程

当前流程：

- 语义处理得到文本
- 队列消费者调用 `embed()`

改造后流程：

- 队列消费者调用 `embed_document()`
- 本地 embedder 自动套用 document 侧规则
- 向量写入时附带与当前模型一致的 collection 元数据

### 检索流程

当前流程：

- retriever 调用 `embed()`

改造后流程：

- retriever 调用 `embed_query()`
- 本地 embedder 自动套用 query 侧规则
- 检索时使用与 document 同体系生成的向量

## 批量 Embedding 策略

第一版可以先保证单条处理正确，但设计上必须明确批量优化路径。

阶段一：

- 在 `LocalDenseEmbedder` 中实现 `embed_batch()`
- 队列层暂时仍允许继续按单条处理

阶段二：

- 在队列侧做消息聚合，一次编码多条待 embedding 文本

如果没有批量能力，本地 CPU 索引构建吞吐大概率会明显低于模型 benchmark。

## 开发顺序

1. 增加 `local` backend 的配置校验和 factory 注册。
2. 增加“缺失 embedding 配置时默认生成 local dense 配置”的逻辑。
3. 实现基于 `llama-cpp-python` 的 `LocalDenseEmbedder`。
4. 增加内置本地模型注册表，并接入 `bge-small-zh-v1.5-f16`。
5. 增加模型路径解析、缓存目录和自动下载逻辑。
6. 增加 `embed_query()` / `embed_document()` 双路接口。
7. 持久化 collection embedding 元数据，并补一致性检查。
8. 增加 rebuild-required 错误流。
9. 增加 `embed_batch()` 和 benchmark 基础设施。
10. 更新用户文档、示例配置和安装说明。

## 测试计划

### 配置测试

- 缺失 `embedding` 时自动生成隐式 local dense 配置
- 显式远程配置时不触发默认本地逻辑
- `model_path` 能覆盖逻辑模型解析
- `cache_dir` 覆盖生效

### 依赖与初始化测试

- 缺少 `llama-cpp-python` 时，启动错误信息正确
- 显式 local backend 且依赖已安装时可成功初始化
- 非法 GGUF 路径会触发模型加载失败
- 下载失败时错误信息完整可读

### Embedding 行为测试

- `embed_query()` 与 `embed_document()` 走不同路径
- 返回维度与模型维度一致
- `embed_batch()` 的结果顺序和数量正确

### 元数据与重建测试

- 首次启动时能生成和当前模型一致的元数据
- 改变 model identity 时会触发需要重建
- 改变 dimension 时会触发需要重建

### 检索回归测试

- 中文 query 能正确召回中文文档
- query/document 双路编码不会破坏现有检索链路
- 现有远程 provider 行为保持不变

### 打包测试

- `pip install openviking` 可以在不安装本地依赖的情况下成功
- `pip install "openviking[local-embed]"` 可以启用本地 import 路径
- 缺少 extra 且触发默认本地行为时，报错应明确，而不是模糊 import failure

## 基准测试

至少记录以下指标：

- 依赖已安装且模型已缓存时的启动耗时
- 首次下载模型时的启动耗时
- 单条 embedding 延迟
- 批量 embedding 延迟
- 在代表性中文语料上的索引构建吞吐

在 benchmark 出来之前，不应假设“默认本地 embedding”在所有环境里都同样合适。

## 运维说明

推荐安装命令：

```bash
pip install "openviking[local-embed]"
```

如果用户想使用远程 embedding：

- 显式配置 `embedding.dense.backend`
- 提供相应 provider 的凭证

## 风险

- 原生依赖安装失败
- 预编译 wheel 覆盖不足
- GGUF 与运行时版本不兼容
- 用户预期“零配置”但实际缺少 local extra
- 模型切换后索引不兼容
- 未做批量聚合时索引吞吐偏低

## 交付物

- 本地 dense embedder 实现
- local backend 配置与 factory 集成
- 内置模型注册表
- 启动期错误提示与安装指引
- collection 元数据校验
- rebuild-required 机制
- 测试和 benchmark 脚手架
- 用户文档更新
