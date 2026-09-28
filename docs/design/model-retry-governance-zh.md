# OpenViking 模型重试治理：add_resource 与 session_commit

状态：2026-09-29，#5301 已改为同一个离线任务共享 R 次额外模型尝试。`RetryBudget.retry_count` 从 0 增长到 R，每次调用的 `RetryContext` 只保存局部尝试、路由和终态，并引用同一个任务预算。第 2 至 5 节描述业务与当前实现；第 1、8 至 10 节保留早期方案与历史验证，其中“每个调用 4 次”和 native 的 20 次请求不是新版本的共享额度验收结果。本次验证记录见第 11 节。

## 1. 建议与收益证据

本节次数来自改造前基线与早期原型，用于解释重试叠乘问题。

模型调用层应成为唯一自动重试负责人。迁移的 Provider/SDK 每次只发送一个请求（Codex OAuth 401 续期重发等兼容例外见第 9 节）；workflow 和 queue 收到模型终止结果后记录失败或按现有规则降级，不再重新获得一份重试预算。在线调用最多一次，离线调用只对可恢复错误做有限重试；credential failover 同样消耗总次数。

一期先覆盖 `add_resource` 的 VLM/Embedding 和 `session_commit` Phase 2。必须同时调整这些链路的失败出口，否则只把 SDK 重试关掉，仍会被外层步骤或消息重入放大。这是模型重试的接入改造，不要求建设通用任务重试、Checkpoint 或集群流控平台。

| 本地故障场景 | 基线 HTTP 请求次数 | 单负责人原型 | 证据范围 |
| --- | ---: | ---: | --- |
| Embedding 持续 429，单次出队处理 | 12 | 4 | 真实 OV adapter + 本机 Ark SDK + mock HTTP |
| Volcengine 文本异步 VLM 持续 401，单凭证 | 4 | 1 | 真实 VLM adapter + mock HTTP |
| Phase-2 步骤重试组合 VLM 持续 429 | 16 | 4 | 组合实际 retry helper/常量和 adapter，未执行完整 commit |
| 上一场景配置两个 credential | 32 | 总计 4 | 实际 MultiCredentialVLM，非线上成功率测量 |

Embedding 的 12 次来自 OV 的 4 次 SDK 调用乘以 SDK 的 3 次 HTTP 尝试。本机 `volcengine-python-sdk==5.0.14` 默认 `max_retries=2`；当前 Embedding 的 Ark 构造没有显式关闭它。VLM 的 Ark SDK 已关闭重试，但文本异步路径自己对所有 Exception 循环，sync/vision 路径则不同。因此历史上的“最多 4 个物理 attempts”只在 SDK 确实单次请求时成立。

Embedding 耗尽内部重试后，handler 仍把同一消息重新入队。除了 transient/unknown，`content_safety` 和 `quota_exceeded` 也落入重入分支；auth 已有明确失败出口。真实 handler 的三轮有界回放复现了这些重入。额度耗尽会打开熔断器，后续轮次可能只等待和重入；熔断只能减速，没有终止预算。

这些证据足以证明结构性重复请求，支持先做收敛。文件摘要、目录 overview、分批与 merge、不同节点向量化属于正常业务扇出，不能以“模型调用数 / add_resource 任务数”衡量重试。历史截图中的 429、积压和累计 Token 也不足以估算重试成本或归因到特定操作。

## 2. 业务中什么时候需要调用模型

模型用于生成或理解内容、计算向量，以及配置启用后的模型重排。读取已有文件、归档消息、写入已生成的向量、等待锁和队列投递本身不需要再次调用模型。一次 OV 请求可能产生多次正常模型调用，不能把它们都算成 retry。以下函数按 #5301 的 `fa3e19439a0977a5ea122709e8d17fa43277b35c` 核对。

### 2.1 资源导入：add_resource

后台入口 `AddResourceProcessor._process()` 绑定 `model_workload("add_resource", root_task_id=msg.task_id)`，再调用 `ResourceService.execute_add_resource_job()`。模型调用发生在后续具体处理环节，而非收到 HTTP 请求时就固定调用一次。

| 业务环节与触发条件 | 具体函数与模型出口 | 哪些是正常调用 |
| --- | --- | --- |
| 解析内容：选用需要视觉或语言理解的解析路径 | `VLMProcessor.understand_image()` / `understand_page()` / `batch_analyze_document()` → `get_vision_completion_async()` 或 `get_completion_async()` | 处理不同图片、页面或批次是新调用；纯文本读取、确定性解析不因此调用模型 |
| 文件摘要：没有可复用的摘要，且需由模型生成 | `SemanticTreeExecutor._file_summary_task()` → `SemanticProcessor._generate_single_file_summary()` → `_generate_text_summary()` → `get_completion_async()` | 不同文件各自生成摘要；增量处理可复用已有摘要，部分代码文件可以直接提取结构而不请求模型 |
| 目录概览：需根据文件摘要和子目录摘要生成或更新概览 | `SemanticProcessor._generate_overview()` → `_single_generate_overview()` / `_batched_generate_overview()` → `get_completion_async()` | 不同目录、分批生成及最终合并都是正常业务生成 |
| 图片、音视频摘要：格式和模型能力允许且启用了对应理解能力 | `generate_image_summary()` → `get_vision_completion_async()`；`generate_audio_summary()` / `generate_video_summary()` → `get_media_completion_async()` | 理解不同媒体是新调用；媒体上传和状态轮询不能直接视为再次生成。音视频全流程尚未统一接入 RetryContext |
| 向量化：创建或更新需要 embedding 的索引内容 | `TextEmbeddingHandler.on_dequeue()` → `embed_compat(..., is_query=False)` → `embed_async()` | 不同索引内容各自生成向量；只改元数据、删除索引或写入已有向量不需要重新 embedding |

这些环节可能扇出或并行，也可能因缓存、增量更新、解析器类型或配置被跳过，不是一条每步必调用一次的固定流水线。解析和媒体 helper 中还存在局部异常降级；调用已接入模型 owner，并不自动证明整条业务链路都已正确传播终态。

代码入口：[资源任务](https://github.com/volcengine/OpenViking/blob/fa3e19439a0977a5ea122709e8d17fa43277b35c/openviking/storage/queuefs/add_resource_processor.py#L230)、[文件摘要](https://github.com/volcengine/OpenViking/blob/fa3e19439a0977a5ea122709e8d17fa43277b35c/openviking/storage/queuefs/semantic_processor.py#L1277)、[目录概览](https://github.com/volcengine/OpenViking/blob/fa3e19439a0977a5ea122709e8d17fa43277b35c/openviking/storage/queuefs/semantic_processor.py#L1743)、[向量消费](https://github.com/volcengine/OpenViking/blob/fa3e19439a0977a5ea122709e8d17fa43277b35c/openviking/storage/collection_schemas.py#L661)、[解析理解](https://github.com/volcengine/OpenViking/blob/fa3e19439a0977a5ea122709e8d17fa43277b35c/openviking/parse/vlm.py#L65)。

### 2.2 会话提交：session_commit

Phase 1 负责归档、状态与投递，不直接调用模型。Phase 2 的 `Session._run_memory_extraction()` 绑定离线 workload；是否生成 Working Memory、抽取记忆或技能由配置、memory policy 和待处理内容决定。

| 业务环节 | 具体函数与模型出口 | 正常调用边界 |
| --- | --- | --- |
| 归档摘要 / Working Memory 创建或更新 | `_run_archive_summary()` → `_generate_archive_summary_async()` → `get_completion_async()` | 为当前归档生成或更新工作记忆；长会话分批时可能多次调用 |
| 长期记忆抽取 | `SessionCompressorV3.extract_long_term_memories()` → `_extract_user_memories()` → `ExtractLoop.run()` → `_call_llm()` → `get_completion_async()` | 模型选择工具、读取结果后进入下一轮是业务交互，不是上一次网络请求的重试 |
| 可选技能抽取及经验更新 | `extract_session_skills()` / `train_from_extracted_cases()` 及其内部抽取、优化流程 | 仅符合配置和输入条件时执行；每个生成步骤可以有独立输入 |
| 抽取结果的摘要和索引 | 后续 SemanticQueue / EmbeddingQueue 中的摘要、概览和 embedding 出口 | 生成不同记忆节点的摘要或向量是正常扇出 |

成功响应的 JSON/工具参数修复、patch repair 由业务循环自己的上限约束，本轮模型故障 retry_count 不代替这些上限。模型调用已经报错或耗尽重试后，不能改称“格式修复”再发同一个请求。

代码入口：[Phase 2](https://github.com/volcengine/OpenViking/blob/fa3e19439a0977a5ea122709e8d17fa43277b35c/openviking/session/session.py#L1942)、[Working Memory](https://github.com/volcengine/OpenViking/blob/fa3e19439a0977a5ea122709e8d17fa43277b35c/openviking/session/session.py#L2921)、[长期记忆入口](https://github.com/volcengine/OpenViking/blob/fa3e19439a0977a5ea122709e8d17fa43277b35c/openviking/session/compressor_v3.py#L419)、[每轮 LLM 调用](https://github.com/volcengine/OpenViking/blob/fa3e19439a0977a5ea122709e8d17fa43277b35c/openviking/session/memory/extract_loop.py#L1090)。

### 2.3 在线查询也可能用模型，但不使用离线重试额度

| 查询能力 | 模型触发点 | 当前范围 |
| --- | --- | --- |
| find / search 的查询向量 | `HierarchicalRetriever.retrieve()` → `embed_compat(..., is_query=True)` | 需要把查询编码为向量时调用 embedding；同请求缓存命中可复用结果。向量库相似度检索本身不再调用生成模型 |
| search 的意图分析、查询展开 | `IntentAnalyzer.analyze()` → `get_completion_async()`；`expand_queries()` 在模式和会话上下文满足条件时使用它 | 可选能力，不是每次 find/search 都要调用 LLM |
| recall 上下文摘要改写 | `rewrite_context()` → `planner.get_completion_async()` | 需要改写且输入非空时生成摘要；超时或失败走已有降级 |
| 模型重排 | `HierarchicalRetriever._rerank_scores()` → `RerankClient.rerank_batch()` | 配置了 reranker 且相应检索模式启用时调用；分数归一化、规则排序不等于调用重排模型。Rerank 尚未接入 #5301 owner |

在线/离线由发起业务的 workload 决定，不由函数名决定。用户直接 find/search 是在线；离线记忆处理内部若发起检索，其中接入统一入口的模型调用应继承该离线任务的共享额度。独立提交的新任务不与旧任务共享。pi/Codex 自己的推理模型不经过 OV，只有它们调用 OV 后触发的上述处理属于本方案。

代码入口：[查询向量](https://github.com/volcengine/OpenViking/blob/fa3e19439a0977a5ea122709e8d17fa43277b35c/openviking/retrieve/hierarchical_retriever.py#L149)、[意图分析](https://github.com/volcengine/OpenViking/blob/fa3e19439a0977a5ea122709e8d17fa43277b35c/openviking/retrieve/intent_analyzer.py#L58)、[上下文改写](https://github.com/volcengine/OpenViking/blob/fa3e19439a0977a5ea122709e8d17fa43277b35c/openviking/retrieve/context_assembler/rewrite.py#L77)、[模型重排](https://github.com/volcengine/OpenViking/blob/fa3e19439a0977a5ea122709e8d17fa43277b35c/openviking/retrieve/hierarchical_retriever.py#L371)。

## 3. 什么时候产生 retry，怎么共享计数

### 3.1 只有失败后的额外模型请求才消耗额度

同一次离线任务共享 `RetryBudget.retry_count`，由模型调用层统一扣减。文件摘要、目录概览、记忆抽取和 embedding 的首次正常请求都不扣；某次请求失败后需要重发或切 credential，再扣一次。成功不清零，换模型、换凭证或进入下游队列不重新获得额度。在线模型故障不自动重试。

| 上一次请求的结果 | 是否可以再请求 | retry_count 口径 |
| --- | --- | --- |
| 短期限流 429、可恢复 5xx、连接错误、超时 | 离线且有剩余额度、未取消或超期时，退避后重试 | 每次额外尝试消耗 1；Retry-After 和等待本身不消耗次数 |
| 401/403、欠费、配额耗尽 | 不重试原凭证；若允许容灾且有其他有效候选，可在离线切换 | 切换后的额外尝试也消耗 1；没有候选则失败上抛 |
| 参数错误、输入过大、内容安全拒绝、unknown | 不自动重试或遍历凭证 | 不产生额外模型请求，直接失败 |
| 取消、deadline 到期或共享额度不足 | 不再重试当前失败调用 | 向上传播终止结果；不能由外层重跑步骤或消息来重置计数 |
| 成功后处理下一个文件、下一批内容或下一轮工具结果 | 属于新的正常业务调用 | 首次请求不消耗 retry_count；它若失败，重试仍用同一任务的剩余额度 |
| 锁冲突、breaker 准入等待、已生成向量的写入失败 | 由对应调度或存储逻辑处理 | 不是模型重试，不能因此重新调用模型 |

错误判断优先使用 provider 明确分类，再看结构化语义 code、HTTP status，最后才解析文本。例如 `400 + AccountOverdue` 是凭证问题，`429 + insufficient_quota` 是配额问题，不能统一按 400 永久失败或 429 短期限流处理。超时后的重试也不等于 provider 一定没有执行上一次请求；retry_count 约束尝试次数，不承诺恰好一次生成或准确计费。

### 3.2 一个具体计数例子

以下说明当前计数规则，假设同一次离线导入共享 `model_retry.max_retries=3`；摘要、概览与 Embedding 跨消息恢复的对应回归见 `tests/unit/test_model_call.py`：

| 顺序 | 业务动作 | 共享 retry_count |
| --- | --- | ---: |
| 1 | a.md 首次摘要成功 | 0 |
| 2 | b.md 首次摘要遇到 429，使用一次额度重试 | 1 |
| 3 | b.md 重试仍超时，再使用一次额度重试，随后成功 | 2 |
| 4 | 目录概览首次生成成功 | 2 |
| 5 | 一次 embedding 首次遇到 503，使用最后一次额度后成功 | 3 |
| 6 | 后续另一个节点首次 embedding 成功 | 3 |
| 7 | 再有模型调用首次失败 | 3，不再重试，按任务既有失败规则结束 |

因此，额度耗尽不等于禁止剩余正常首次调用；若任务已经因某个不可恢复错误进入失败或取消状态，则由既有任务逻辑停止后续调度。若正常业务需要 M 次模型调用，共享额外额度为 R，则在每次尝试恰好发出一次模型请求的路径上，最多为 M + R 次，而非每个调用各自乘以 1 + R。首次模型调用数 M 仍受输入规模及业务循环上限影响，不能把共享 retry_count 宣传成整个任务的固定请求数上限。

## 4. 当前结构：任务共享预算，每次调用独立状态

`retry_count` 放在 [RetryBudget](../../openviking/utils/retry_budget.py)，`RetryContext.budget` 引用它。这里的“一次模型调用”指一次具体生成或向量计算，例如为 b.md 生成摘要；它可经历首次尝试、429 后重试和切 credential。它不是整个 HTTP 请求，也不是整个 add_resource 任务。

| 层次 | 具体函数 | 保存和传递什么 |
| --- | --- | --- |
| 任务入口 | `AddResourceProcessor._process()`；`Session._run_memory_extraction()` | `model_workload(..., root_task_id=task_id)` 确定离线策略与任务预算 |
| 同进程任务索引 | `TaskWorkIndex.retry_budget()`；`TaskTracker.model_retry_budget()` | 按任务 ID 获取同一个 `RetryBudget`，含 `max_retries` 和已用 `retry_count` |
| 队列与 DAG | `TaskWorkQueueMiddleware.process()`；`SemanticTreeExecutor._run_work_with_context()` | 绑定相同任务索引，恢复各 executor 捕获的 workload/budget，避免共用 worker 串用其他任务的上下文 |
| 模型尝试 | `run_model_sync/async()` → `RetryContext.before_attempt()` | 首次不扣；退避结束、额外 adapter 尝试开始前，以线程锁原子扣 1；竞争失败保留原异常，不记虚假 attempt |
| 单次调用状态 | `RetryContext` | 自己的 attempts、route、disabled_routes、deadline、logical_call_id 与终态；并行调用不共用这些可变字段 |

SemanticMsg / EmbeddingMsg 传递任务 ID、operation 和可选 deadline；同进程消费者按任务 ID 取回共享对象，不复制整数。成功、完成当前队列步骤、ACK 失败或同进程重投都不清零；预算随任务记录淘汰或删除而清理，有未完成队列/活跃工作时保留。正常首次调用仍可执行，所以投递重放产生的首次请求数不由 R 单独约束；本方案不提供恰好一次处理。

配置在 `ov.conf` 顶层，默认整个任务共享 3 次额外尝试，`0` 禁止额外尝试：

```json
{ "model_retry": { "max_retries": 3 } }
```

预算首次绑定任务时固定。`embedding.max_retries` / `vlm.max_retries` 为兼容旧路径仍可读取，但不再为已迁移的 owner 路径发放独立额度；原来用它们禁用重试的部署应改设 `model_retry.max_retries=0`。无 task ID 的显式离线 workload 在本 scope 内共享预算，独立任务各有一份；在线调用仍最多一次。配置是进程级策略，没有新增按账号动态预算或集群共享服务。

SDK/transport 隐式重试继续关闭。Session 不因模型错误重跑整个抽取步骤；Semantic/Embedding 模型终止沿用 FAILED/ACK 出口。breaker 准入等待、退避期间取消、锁等待不消耗 retry_count。同步 I/O 中断仍依赖 transport timeout；deadline 可选，没有新增默认整任务期限。

浩杰的 [#5364](https://github.com/volcengine/OpenViking/pull/5364) 已随 main 进入 #5301；本次继续保留 Working Memory 直接传播模型异常的结构，删除通过错误文本猜测能否 fallback 的逻辑。create/update/fallback 中未带 marker 的 unknown 同样上抛，成功响应的格式解析仍按业务规则处理。#4661 的结构化分类与 unknown fail-fast 已定向吸收，保留 Qin Haojie 的 co-author。最新 main 的 Gemini client 生命周期和 extra_request_body 改动也已合入；Gemini 继续每次请求独立 client 且 SDK attempts=1。

当前主要覆盖非流式 VLM text/vision 和 Embedding。完整音视频、流式、Rerank、第三方 adapter 仍需逐路径迁移验证。共享预算只在同进程、任务记录保留期间有效，不跨 Pod/重启持久化，不包含客户端重新提交的新任务；它也不是共享限流或熔断服务。

代码依据：[模型 owner](../../openviking/utils/model_call.py)、[任务索引](../../openviking/service/task_work_index.py)、[队列绑定](../../openviking/service/task_queue_middleware.py)、[DAG 恢复](../../openviking/storage/queuefs/semantic_executor.py)。

## 5. Metrics：现有实现的计数口径

保留现有 VLM/Embedding/operation Token 指标及四个模型 family。任务共享额度由测试直接校验 RetryBudget 与请求数；Prometheus 仍按每次模型调用和尝试归因，不作为预算账本。旧 calls 指标按 SDK 调用周围打点，SDK 内部再发请求可能不可见，不能直接当作物理 HTTP 计数。

| 指标 | labels | 何时计一次 |
| --- | --- | --- |
| `openviking_model_logical_calls_total` | model_type, operation, stage, result | 一个 logical call 最终结束；重试中间失败不计新 call |
| `openviking_model_attempts_total` | model_type, operation, stage, result, error_class | 单次 adapter 请求结束；需先验证 adapter 与 transport 1:1 |
| `openviking_model_retry_decisions_total` | model_type, operation, stage, decision, reason, owner | retry/failover 在扣减成功并开始额外尝试时记录；stop 在终止时记录 |
| `openviking_model_retry_exhausted_total` | model_type, operation, stage, reason | call 因 retry_budget/deadline/backoff_limit 结束，仅一次；retry_budget 取代旧 max_attempts reason |

operation 统一为 `add_resource` / `session_commit` / `find` / `search` 等固定枚举。当前源码已有 `resources.add_resource`、`add_resource_job`、`session_commit_phase2` 等名字，需要明确映射；队列不能只依赖进程内 telemetry 对象，恢复时从消息继承。stage 同样固定枚举。result、error_class、reason、owner 用受控值；task/session/logical_call/credential ID 以及原始异常内容只进日志或 trace，不进 label。

文件摘要和目录概览通过独立的 `model_stage` 上下文标记为 `file_summary` / `directory_overview`，优先于内层 legacy telemetry stage；未显式标记时沿用现有阶段归因，例如 session 的 `archive_summary`。该上下文只影响新模型指标，原有 operation Token 的 `semantic_execute` 标签保持不变，不修改次数、deadline 或 credential 委托。

Dashboard 先展示 attempts / logical calls 与 exhausted / logical calls，按 operation 和 stage 拆分。两类计数都按结束记录；短窗口因跨窗/在途会有偏差，精确收益使用同一批已完成 logical calls 的回放数据，不将分钟级比率当账单或严格不变量。进程崩溃可能丢失进程内事件，Prometheus 也不是预算账本。

若展示重试 Token 占比，再补 `openviking_model_attempt_tokens_total{model_type,operation,stage,attempt_kind,token_type}`，attempt_kind 至少区分 initial/retry/failover。只累加 provider 真实返回的 usage，并展示 usage 覆盖情况；没有 usage 的失败请求标为未知，不能记作零成本。未接入该指标前不展示“已节省 Token”。不依赖客户部署的私有 Metrics，也不新增遥测回传。

## 6. 验证、实施与回滚

最初的独立原型用于固定策略契约和基线 HTTP 回放；生产实现落地后已移除该重复代码与生成结果。当前契约由实际 adapter、队列、Phase 2 和 Metrics 测试覆盖；验证结果和未覆盖路径见第 8 至 10 节。隔离 K8s 中还完成了 7 个 native 服务/队列场景；HTTP ingress、跨进程恢复、完整长期记忆抽取与线上灰度仍未覆盖，未发布共享服务。

| 顺序 | 交付与验收 |
| --- | --- |
| A：观测与基线 | 补四个 family；完成已支持 provider 的 sync/async 单次请求契约清点；保留本地故障回放 |
| B：两个场景接入 | 统一 owner，关闭迁移路径 SDK 重试，同时去掉 workflow/queue 的模型重试；永久错误 1 次、在线 1 次、离线总计不超过 N |
| C：真实流程验证 | 故障注入覆盖 429 后成功、持续失败、混合 credential、取消、breaker/deadline；核查失败终态、锁/等待释放、成功步骤和向量不重复生成 |
| D：恢复保证与灰度 | 如需跨重启次数硬上限，完成预占持久化和各崩溃窗口测试后再承诺；自有环境比对成功率/产物、额外请求、延迟和已知 usage |

先小范围按 provider 与操作灰度。回滚必须成组处理 SDK、模型 owner 与外围失败出口，不能只打开旧 SDK retry 又保留新 owner，也不能恢复无限模型失败 requeue。共享额度使用 model_retry.max_retries；deadline 的生产数值按自有环境完成率和延迟确定，原型的 60 秒仅为测试值。

## 7. 代码依据

以下链接固定到本次检查的 commit，避免后续 main 漂移影响评审。

- [Embedding 重试 helper 调用](https://github.com/volcengine/OpenViking/blob/611f5c469b2bb8dc6d072b215251379e780d3f23/openviking/models/embedder/base.py#L343)；[Ark Embedding client 构造](https://github.com/volcengine/OpenViking/blob/611f5c469b2bb8dc6d072b215251379e780d3f23/openviking/models/embedder/volcengine_embedders.py#L82)。
- [Embedding 的终止与重新入队分支](https://github.com/volcengine/OpenViking/blob/611f5c469b2bb8dc6d072b215251379e780d3f23/openviking/storage/collection_schemas.py#L735)；[Semantic 错误重新入队](https://github.com/volcengine/OpenViking/blob/611f5c469b2bb8dc6d072b215251379e780d3f23/openviking/storage/queuefs/semantic_processor.py#L691)。
- [Volcengine 文本异步重试](https://github.com/volcengine/OpenViking/blob/611f5c469b2bb8dc6d072b215251379e780d3f23/openviking/models/vlm/backends/volcengine_vlm.py#L271)；[多凭证切换](https://github.com/volcengine/OpenViking/blob/611f5c469b2bb8dc6d072b215251379e780d3f23/openviking/models/vlm/base.py#L874)。
- [Phase-2 外层步骤重试](https://github.com/volcengine/OpenViking/blob/611f5c469b2bb8dc6d072b215251379e780d3f23/openviking/session/session.py#L2650)；[已有终态恢复](https://github.com/volcengine/OpenViking/blob/611f5c469b2bb8dc6d072b215251379e780d3f23/openviking/session/session.py#L2237)；[ExtractLoop 业务再生成](https://github.com/volcengine/OpenViking/blob/611f5c469b2bb8dc6d072b215251379e780d3f23/openviking/session/memory/extract_loop.py#L292)。
- [摘要局部降级](https://github.com/volcengine/OpenViking/blob/611f5c469b2bb8dc6d072b215251379e780d3f23/openviking/storage/queuefs/semantic_executor.py#L1087)；[QueueFS dequeue/ACK 恢复语义](https://github.com/volcengine/OpenViking/blob/611f5c469b2bb8dc6d072b215251379e780d3f23/openviking/storage/queuefs/named_queue.py#L223)；[TaskWorkIndex 的运行时边界](https://github.com/volcengine/OpenViking/blob/611f5c469b2bb8dc6d072b215251379e780d3f23/openviking/service/task_work_index.py#L3)。
- [现有 Embedding Metrics](https://github.com/volcengine/OpenViking/blob/611f5c469b2bb8dc6d072b215251379e780d3f23/openviking/metrics/collectors/embedding.py#L121)；[现有 VLM Metrics](https://github.com/volcengine/OpenViking/blob/611f5c469b2bb8dc6d072b215251379e780d3f23/openviking/metrics/collectors/vlm.py#L93)。


## 8. 首版实现、回归结论与后续准入

维护约定：以本方案作为策略评审依据；后续改变错误分类、次数、切换、失败出口或指标口径时，同一变更同步修改仓库文档和飞书方案，更新验证证据。

### 首版历史边界（已由第 4 节的任务预算替代）

开发分支为 `feat/model-retry-governance`。`openviking/utils/model_call.py` 提供 sync/async 唯一 owner；`RetryContext` 保存单次 logical call 的 identity、root task 归属、共享次数、route、deadline 和终态，工作上下文与单次调用状态分开保存，不修改共享模型实例。credential wrapper 只向选中的 adapter 显式委托一次请求；委托绑定 adapter 与当前执行线程/任务，消费后失效。独立子调用、并行任务和不同模型各自建立 logical call，不因处于同一调用栈而跳过预算。未绑定上下文时按在线处理，最多 1 次；两个后台入口明确绑定 offline，使用原配置 `max_retries + 1`，默认共 4 次。该 context 仍为进程内对象，root task 只用于日志与 trace 关联，不代表跨 worker 的持久次数账本。

当前覆盖 OpenAI / Volcengine / LiteLLM 的非流式 text / vision、Embedding 公共调用入口，以及实际配置使用的 MultiCredentialVLM / FailoverEmbedder。多凭证成功路由仍保持 sticky；短暂错误在候选凭证间切换，auth / quota 错误禁用当前 call 内的失败凭证，均消费统一总次数。原始 SDK 异常类型、status 与 body 保留，通过附加 `model_call_error` 终止信息阻止外层重新获得预算；无上游异常的 deadline / breaker 拒绝使用 ModelCallError。

Ark、OpenAI-compatible Embedding、Gemini、MiniMax 和 LiteLLM 的可见隐式重试已显式关闭。HTTP 请求次数已用真实 SDK 与本地 HTTP 故障注入验证 Ark Embedding、Volcengine VLM、OpenAI VLM、Gemini Embedding 及 MiniMax 同步 Embedding 的目标场景；Cohere 同步入口也已接入 owner，并补充空消息 transport timeout 的统一分类；其他 provider 不能仅凭配置修改就声称通过 transport 一对一验证。流式、音视频上传/轮询/生成流程、Codex 401 刷新重发、旧 FailoverVLM 直接调用，以及第三方 adapter 仍需单独迁移/验证；配置中的旧 backup 语法已由工厂转换到 MultiCredentialVLM，不等同于直接使用旧 wrapper。

Embedding 与 Semantic 共用有限的熔断准入等待；等待结束仍被拒绝才返回 FAILED 并通知 wait tracker。Semantic 提前拒绝或取消时释放尚未接管的移交锁，开始执行后不因模型或存储异常重放整条消息。单条输入的参数、过长与内容安全错误不再影响共享 breaker 健康状态。文件摘要原有空摘要降级仍保留。Session Phase 2 去掉整步骤重试，working-memory creation/update 不再吞掉终止的模型错误；失败写 `.failed.json`，保留已完成步骤，恢复看到终态后直接结束。

四个指标已通过现有 datasource / collector 路由导出，operation / stage 为固定枚举，call ID 留在异常及日志。当前 attempts 是进入 adapter 的尝试数：本地准备失败、等待 semaphore 时取消，或特殊 SDK 内部认证重发，都可能使它与 HTTP 请求数不完全相等。只在已验证 transport 契约的路径上用它近似物理调用放大，不将其当计费账本。Retry-After 在 30 秒等待上限内被尊重，超过则以 backoff_limit 终止，避免无限等待或提前重试；该上限是首版策略值，后续需结合离线完成率评估。

MiniMax 同步 HTTP 使用 `HTTPAdapter(max_retries=0)`，不保留 urllib3 的状态重试规则，由 `response.raise_for_status()` 保留 `HTTPError.response` 和 `Retry-After`。否则即使 `total=0`，状态重试规则仍会把 429/503 包装为没有响应头的 `RetryError`，导致统一 owner 跳过服务端要求的等待。该 adapter 沿用统一的 30 秒等待上限，不新增 provider 专属策略。

### 首版历史验证结果

| 验证 | 结果 | 限制 |
| --- | --- | --- |
| 首版聚焦回归 | 276 passed，2 skipped | 覆盖 owner、HTTP 请求数、队列终态、等待/锁、Phase 2、异常类型与指标；跳过项为既有环境条件 |
| 真实 SDK + mock HTTP | 19 个场景通过；离线持续 429 共 4 次、在线共 1 次、双 credential 共 4 次、单 credential 401 为 1 次；429 后恢复成功 | 无真实模型、账单或客户线上流量 |
| Phase 2 独立流程 | 模型失败 4 次、抽取步骤执行 1 次；写失败标记并保留完成记录；恢复不重跑；WM creation/update 不触发额外 fallback | 内存文件系统和 TaskStore，未覆盖 native engine / 真 QueueFS 崩溃恢复 |
| 扩展回归 | 879 个测试：856 passed、20 failed、3 skipped；20 个失败已在干净基线复现 | 既有 Ollama 参数 / max_tokens 和日志捕获断言问题，本次不改动其功能 |
| Gemini 扩展测试 | 首版未安装 google-genai；本轮已在隔离依赖目录补测，结果见第 9 节 | 原工作环境未修改；使用 google-genai 2.24.0，其他 SDK 版本仍需各自验证 |
| 本地完整 session 集成 | macOS 的 3 个目标场景在初始化阶段被 PersistStore 缺失阻塞；已另用 K8s native runtime 补验 7 个服务/队列场景，见第 10 节 | 仍不能声称完整产品 E2E 通过或已无生产回归 |

### 会改变什么，以及如何判断能否合入

| 回归风险 | 原因与首版行为 | 合入前重点观察 |
| --- | --- | --- |
| 短暂故障下成功率下降 | 在线不再多试；离线单次模型调用默认 4 次比过去 12/16/32 次更少；超过 4 个坏 credential 不会继续遍历到末尾 | 同一批固定输入对比产物、成功率、额外请求和尾延迟；允许调整有上限的配置，不恢复叠乘 |
| 熔断等待与故障窗口完成率 | 本轮修正为当前 delivery 有限等待，取消可退出；等待期间会占用 consumer 槽位，到期仍失败 | 测试冷却后恢复、并发故障不延长本条等待、deadline 与取消；K8s 最后验证 worker 占用和尾延迟，不恢复无限重入 |
| 工作流错误暴露更明确 | Session 模型终止不再静默变成占位摘要；Session 和 Semantic 开始执行后的存储临时错误也可能直接失败 | 确认失败状态和重新提交体验；只在具体幂等存储 I/O 处补重试，不能恢复整个抽取函数重跑 |
| 错误类型/锁/正常链路兼容 | 回归中已修复 SDK 异常类型被覆盖、breaker 提前失败未释放移交锁两项；native 成功、错误终态、取消和锁释放已验证 | native 并发 add_resource、commit 前序等待、向量写入故障与进程崩溃仍需独立验证 |
| 覆盖不全与跨重启预算 | 部分 provider/媒体/流式路径尚未验证；无 durable attempt 预占，无默认总 deadline，无 operation 全局 retry quota | 不对所有 SDK 或跨任意重启承诺物理次数上限；下一步按实际流量补齐，不新增通用调度平台 |

这版可供代码评审和受控环境验证。先完成本地契约、adapter、队列与 agent 集成回归，再做 native / K8s 故障测试和灰度；K8s 放在最后，不以本地 mock 成功作为直接上线依据。

## 9. 整体复核：统一边界、模型扩展与 agent 接入

统一的是一次模型生成的重试职责，而不是把所有“再执行一次”都改成同一个循环。新模型接入统一 owner；新 agent 复用 OV 服务端的 owner。正常业务扇出、认证恢复、任务投递、流式输出和存储恢复各有自己的语义，不能用一个嵌套开关一并跳过，也不能互相重新发放模型预算。

### 新模型的接入契约

| 边界 | 契约与验收 |
| --- | --- |
| 一次请求 | adapter 执行一次 I/O，关闭 SDK/HTTP transport 的自动重试；sync/async 都接 owner，不能只改一边 |
| 明确委托 | 当前 credential wrapper 向选中 adapter 委托一次；backend 通过 `adapter=self` 接入。单模型、多 credential 使用同一错误分类和次数规则 |
| 独立生成 | dense/sparse 两种模型、工具下一轮、独立子任务各有自己的 logical call；不把业务组合函数当成单次 transport callback |
| 错误与结果 | 保留 SDK 异常类型、status、body/cause 和成功结果；分类不能依赖 adapter 自行编造的提示。网络错误按类型识别，真实 quota 与短期限流分开 |
| 回归契约 | 至少验证成功、短暂失败后成功、持续 429/timeout、永久错误、双 credential 总预算、在线一次、取消与 deadline；用真实 SDK + mock transport 数请求 |
| 特殊协议 | 媒体上传/轮询不等于再次生成；流式已输出后不重放。认证续期等多请求 adapter 必须显式列出例外，完成 transport 计数验证后才能声明物理请求硬上限 |

本轮修复了共享 bool 将独立嵌套调用误当成同一 attempt 的问题，补齐 Cohere 同步入口、httpx/requests 空消息网络异常分类，以及 Gemini 429 包装提示导致单 credential 与多 credential 分类不一致的问题。没有增加 provider 专属的重试循环，也没有扩大成通用调度框架。

### pi / Codex 能得到什么

| 接入方式 | 本方案覆盖 | 不应混为一谈的边界 |
| --- | --- | --- |
| pi、Codex 记忆插件调用 OV | 同一服务端 `add_resource` 摘要/向量化、`session_commit` 后台记忆抽取；无需每个 agent 再实现一套模型策略 | 宿主 pi/Codex 自己的推理模型不经过 OV owner；Codex 本地压缩 CLI 同样不在其中 |
| 插件 pending queue / HTTP client | 服务端一次任务执行内的模型预算仍生效 | 网络响应丢失后的请求重投属于投递恢复；现有 commit 接口无请求幂等键，不能承诺跨多次提交共享次数预算 |
| OV 配置 `provider=openai-codex` | 其非流式调用通过 OpenAI owner，模型限流/网络错误使用同一规则 | 当前 401 允许 OAuth 刷新后重发一次，保留原有登录兼容性；一次 adapter attempt 可能有两次 HTTP。这与 Codex 记忆插件是两条独立链路 |

pi 正式集成还存在一个与本次重试无关的既有竞态：新 commit pending 时，上一份非空 overview 可能推动本地上下文截断。已在干净基线复现并单独登记 [Bug #5299](https://github.com/volcengine/OpenViking/issues/5299)。该修复已移出本次分支；不能把原始归档仍在磁盘等同于 agent 当前上下文完整。后续独立修复应校验本次 commit 对应的 task/archive，再推进边界。

### 本轮直接回归防护

熔断准入使用现有冷却窗口作为有限等待边界，取消立即传播；与模型 retry、QueueFS requeue 分开。Semantic 开始执行后失败不重放整个消息，避免存储错误再次生成已成功摘要。单 worker 同样走现有 drain/cancel 退出路径，防止新增等待延长停机；取消中的 delivery 不 ACK，沿用既有恢复语义。

这仍有明确取舍：有限次数可能降低长故障窗口的成功率；准入等待占用 worker；Semantic/Session 的存储错误会更早成为可见失败。验收应同时看最终产物、失败状态、正常任务延迟和重复调用，而不是只看 attempts 下降。跨重启硬预算、客户端提交幂等、媒体/流式迁移与 Codex OAuth 请求统一计数是不同的后续工作，本次不声称已完成。

本轮最终聚焦回归 **191 passed**，覆盖统一 owner、43 个真实 SDK/HTTP transport 故障场景、Semantic 终止与锁、单/多 worker 停机及初始化恢复、Phase 2、Codex 兼容和指标。扩展回归共 **1068 项：1041 passed、24 failed、3 skipped**；24 个失败用完全相同的 node ID 和依赖环境在干净基线全部复现，涉及已有 Ollama 参数/max_tokens、Gemini 配置校验大小写、日志捕获和旧 auth 分类断言，未混入本次修复。新增 Gemini 依赖隔离安装，不修改原工作环境；ruff、格式与 diff 检查通过。

K8s 按约定在本地与 adapter 回归之后执行。按部署文档新建独立 context 和个人测试 namespace，7 个 native 场景全部通过，测试 Pod 已清理；未修改共享 QA 服务。新增阶段归因问题已修复，并通过 86 项针对性回归和 native 指标断言，详见第 10 节。上述证据不等于生产回归保证。

## 10. 最终 native 验证与回归边界

2026-09-22 在隔离 K8s Pod 执行 `test_scripts/model_retry/native_e2e.py`，测试 Python 源码为 `a13c3e6b213f3f14d01633d99661c4c8d7e7fd14`，1650 个已跟踪文件 SHA-256 一致。运行环境为 Linux/Python 3.13.15、OpenAI SDK 2.24.0、httpx 0.28.1；native 二进制取自已有 OpenViking 0.4.22.dev95 镜像，并非从该提交重新编译。分场景计数与阶段断言已人工核验；生成的 JSON 输出不纳入版本控制。

实际使用 RAGFS、SQLite QueueFS、filesystem PathLock、本地向量引擎与真实 OpenAI SDK；HTTP 故障由 Pod 内 loopback 模型服务注入，无真实模型费用。初始化的 24 次目录 Embedding 先排空、单独记账，再注入目标操作故障；正常业务扇出与重试明确分开。

| native 场景 | 逻辑调用与实际 HTTP | 结果 |
| --- | --- | --- |
| add_resource 成功 | VLM 3 次、Embedding 5 次，均无重试 | completed，队列排空，资源树锁可重新获取 |
| Embedding 持续 429 | 5 个 logical calls，共 20 次 HTTP，每个 4 次 | failed，零模型失败 requeue，终态后无新请求，锁释放 |
| Embedding 持续 401 | 5 个 logical calls，共 5 次 HTTP，每个 1 次 | failed，零模型失败 requeue，终态后无新请求，锁释放 |
| session_commit 成功 | 1 个 summary call，1 次 HTTP | 实际 SessionCommit consumer 完成，`.done` 存在 |
| Phase 2 持续 429 | 1 个 summary call，4 次 HTTP | failed，仅 `.failed.json` 存在，未重跑整步骤 |
| Phase 2 持续 401 | 1 个 summary call，1 次 HTTP | failed，仅 `.failed.json` 存在，未重跑整步骤 |
| add_resource 取消 | 正在进行的首个 VLM 请求中取消 | cancelled，队列排空，资源树锁释放 |

native 验证发现文件/目录的新指标曾被内层 legacy `semantic_execute` 覆盖；已通过独立 `model_stage` 上下文修正。本次成功导入记录 1 个 `file_summary`、2 个 `directory_overview`，原 Token 指标仍为 `semantic_execute`；session 仍记为 `archive_summary`。修复不改变调用预算或原仪表盘标签，并通过 86 项 owner/executor/metrics/session 回归。

以上是 native 服务入口和真实后台队列的集成验证，不是完整产品 E2E：未覆盖 HTTP server/Ingress、多 Pod、Redis、进程崩溃、真实模型互通、长期记忆 ExtractLoop 协议与质量。Session 场景仅开启 working-memory summary；有限重试对长故障窗口成功率的影响仍需灰度观察。测试 context 保留供后续使用，临时测试 Pod 已删除；共享 QA 服务未改动。

## 11. 2026-09-29 任务共享额度验证

本次在合并 main 的 `a2a5522a3` 基础上实现任务预算，并清理合并遗留的 Semantic 测试冲突标记。最终聚焦回归 **545 passed**，包含 owner、真实 SDK transport、任务索引与队列、DAG 隔离、配置、metrics 和 Session 恢复；Ruff、格式和 diff 检查通过。顶层配置另验证 0、3、7 均可解析。

| 契约 | 验证 |
| --- | --- |
| 摘要 → 目录概览 → Embedding 共享 | SemanticMsg/EmbeddingMsg 序列化恢复后仍引用原 budget；成功不清零，不受 adapter 的 max_retries=99 影响 |
| 并发不超支 | 12 个异步调用争最后 1 次额度，总 attempts=13，额外 metric=1；8 个混合 sync/async 线程共享 R=3，总 attempts=11 |
| ACK 失败与队列切换 | NamedQueue 真实 middleware + mock AGFS；首次成功耗 1，ACK 失败重投再耗 1，下游队列只剩首次尝试，3 个调用共 5 次；pending work 保护预算，记录删除后可释放 |
| 取消和隔离 | 退避取消不扣；在线不消费离线额度；独立任务不共享；共用 Semantic worker 恢复各 executor 的预算 |
| WM 失败出口 | create/update/fallback 的 400、503 和 opaque unknown 均原样上抛，不再通过 fallback 发新请求 |

扩展 Session 回归 **518 passed / 3 failed**（与上述聚焦套件部分重叠，不相加）。3 个失败在未修改的 `a2a5522a3` 使用相同 node ID 全部复现：schema mock 缺 identity_fields、文件系统 mock 缺 exists、初始化模板尾行空格断言；不是本次任务预算引入。复现 node ID：

- `tests/unit/session/memory/test_extract_loop_match_text.py::TestFinalOperationsHydration::test_run_logs_final_operations_after_old_memory_file_is_hydrated`
- `tests/unit/session/test_event_tag_concurrency.py::test_commit_uses_event_tags_from_lock_protected_meta_snapshot`
- `tests/unit/session/test_memory_policy.py::test_initialize_memory_files_renders_fields_without_init_value_as_empty`

本次未重跑 native/K8s 或真实模型服务。native_e2e.py 已把持续 429 的断言从 4×M 改为 M+3，并显式配置顶层额度，但第 10 节的旧 native 结果不能作为新预算的运行证据。

2026-09-23 修复 PR 评审发现的 MiniMax 同步响应头丢失问题。新增 10 个真实 requests/urllib3 与 loopback HTTP 场景：429/503 携带 `Retry-After: 60` 时只发送 1 次并以 `backoff_limit` 终止；携带 2 秒或 30 秒时，校验 owner 先选择对应等待值再发送恢复请求；无响应头时离线仍最多 4 次、在线和 401 仍 1 次。测试替换 owner 的 sleep 记录等待值，不进行真实的 30 秒等待；传输层保持真实。修复前 6 项失败、4 项通过，修复后这 10 项及相关 owner/provider/config 回归共 85 项通过。此次未重跑 K8s/STG，前述 7 个 native 场景仍对应 `a13c3e6b2`，不能作为本次修复的 STG 验收结果。

2026-09-24 补齐合入前评审发现的边界：外层 rate-limit 循环先识别 owner 终态，不能重新发放预算；错误分类按“provider 显式分类、结构化语义 code/type、HTTP status、文本兜底”的顺序决策；Gemini 的明确 invalid-key HTTP 400 与 MiniMax 官方无歧义业务错误码在进入 owner 前归一化。异步 callback 即使吞掉取消并返回，owner 也会拒绝 deadline 后的结果；deadline 和外部取消均立即传播，已取消 callback 只在后台回收。root task 归因随 Embedding/Semantic 队列消息序列化和恢复，但不持久化 attempts 或 logical-call ID。breaker 的 HALF_OPEN 使用单探针租约及 generation 校验，其余并发请求在探针完成前拒绝，旧 generation 的迟到结果不能改变当前状态；取消或早退通过 finally 放弃未消费租约。上述调整不扩大流式、媒体、Rerank 或跨进程持久预算的范围。
