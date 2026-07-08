# Trajectory / Experience 经验学习框架重构
>
> 目标：把真实或离线环境中的 agent rollout 转换为 trajectory，再从 trajectory 估计 experience 更新信号，最终通过可审查、可合并、可并发安全的 policy update 机制更新 `experiences` 目录。

## 1. 总体定位

当前框架把 `experiences` 目录视为一个可优化的 **Experience Policy Set**：

```text
viking://user/<user>/memories/experiences/
```

目录中的每个 experience 文件是一个 `Experience`，整个目录共同构成 agent 的经验策略。训练框架不直接绑定某个 agent loop；它只约束以下抽象链路：

```text
CaseLoader
  -> RolloutExecutor
  -> PolicyTrainer
       -> RolloutAnalyzer
       -> GradientEstimator
       -> PolicyOptimizer
       -> PolicyUpdater
```

其中 `PolicyTrainer` 是训练入口。默认本地实现会在进程内执行 `analyze -> estimate -> plan -> apply`；远程实现可以把 rollout 通过 `session.commit` 提交给 OpenViking 服务端，由服务端完成分析和训练。

### 1.1 训练执行细节图

<img src="https://gist.githubusercontent.com/chenjw/c2de3083d0e1dac3a192c74f98c020c7/raw/502e01c5e207ce8b2b4076a6cd84b8fe9dc06543/train-execution-details.svg" alt="OpenViking session.train 训练执行细节" width="100%">

这张图强调三个实现边界：

- **并行边界**：case rollout、rollout analysis、gradient estimation 可以并行。
- **串行边界**：`ExperienceSet.lock()` 内的 `reload -> PolicyOptimizer.plan -> PolicyUpdater.apply` 必须串行。
- **存储边界**：trajectory 写入发生在 `RolloutAnalyzer`；experience 读取/合并/写入发生在 optimizer/updater；session archive 和 `memory_diff.json` 只出现在 `session.commit` 路径。
- **LLM 边界**：红色特殊框表示该模块会调用 LLM / `ExtractLoop`，包括 trajectory 抽取、experience gradient 估计和 patch merge。


## 2. 代码结构

当前模块结构：

```text
openviking/session/train/
  context.py          # PipelineContext / ExecutionContext
  domain.py           # domain dataclass
  engine.py           # PolicyTrainingEngine：共享 analyze/estimate/plan/apply 内核
  gradients.py        # PatchSemanticGradient
  interfaces.py       # Protocol 接口
  pipeline.py         # OfflinePolicyOptimizationPipeline

  components/         # 可替换组件实现
    case_loader.py
    gradient_estimator.py
    memory_store.py
    policy_optimizer.py
    policy_trainer.py
    policy_updater.py
    remote.py
    rollout_executor.py
    session_commit.py
    snapshotter.py
    trajectory_analyzer.py
```

设计边界：

- 根目录保留框架内核、domain、接口和编排。
- `components/` 放所有具体实现。
- `openviking.session.train` 顶层继续导出常用类，便于外部使用。

## 3. 核心 Domain Model

### 3.1 Experience / ExperienceSet

`Experience` 对应 experiences 目录下的一个 experience 文件。

```python
@dataclass(slots=True)
class Experience:
    name: str
    uri: str
    version: int
    status: PolicyStatus
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    links: list[dict[str, Any]] = field(default_factory=list)
    backlinks: list[dict[str, Any]] = field(default_factory=list)
```

`ExperienceSet` 是某个 experiences 根目录的快照：

```python
@dataclass(slots=True)
class ExperienceSet:
    root_uri: str
    policies: list[Experience]
    metadata: dict[str, Any] = field(default_factory=dict)
    viking_fs: Any | None = field(default=None, repr=False, compare=False)
    request_context: Any | None = field(default=None, repr=False, compare=False)
```

当前实现中，`ExperienceSet` 还负责提供并发安全能力：

```python
async with policy_set.lock():
    latest_policy_set = await policy_set.reload()
```

约定：

- `root_uri` 是 experiences 目录 URI。
- `links/backlinks` 对应 memory file 中的 `MEMORY_FIELDS.links/backlinks`，用于在 train 域快照内保留 v2 link 协议数据。
- `policies` 是当前目录下所有 experience 文件解析后的快照。
- `viking_fs` / `request_context` 是运行时依赖，用于 `lock()` 和 `reload()`，不参与 equality/repr。
- `PolicyTrainingEngine.plan_and_apply(...)` 会先加 policy tree lock，再 reload 最新 policy set，然后 plan/apply。

### 3.2 Trajectory

`Trajectory` 是从 rollout 中抽取并持久化的可训练轨迹样本，对应 trajectories 目录下的 memory 文件。

```python
@dataclass(slots=True)
class Trajectory:
    name: str
    uri: str
    content: str
    outcome: TrajectoryOutcome | str
    retrieval_anchor: str
    metadata: dict[str, Any] = field(default_factory=dict)
```

约定：

- `Rollout` 是原始执行记录。
- `Trajectory` 是从 rollout messages 中抽取出的训练样本。
- trajectory 文件由 `TrajectoryRolloutAnalyzer` 通过 `ExtractLoop + MemoryUpdater` 写入 `memories/trajectories`。

### 3.3 Case / Rubric

`Case` 是可执行、可复现、可评估的训练/评测样例。

```python
@dataclass(slots=True)
class Case:
    name: str
    task_signature: str
    input: dict[str, Any]
    rubric: Rubric
    metadata: dict[str, Any] = field(default_factory=dict)
```

`Rubric` 定义“什么叫做好”和“怎么检查”。当前不再保留独立 `Outcome` 概念。

```python
@dataclass(slots=True)
class Rubric:
    name: str
    description: str
    criteria: list[RubricCriterion]
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass(slots=True)
class RubricCriterion:
    name: str
    description: str
    required: bool
    weight: float
    metadata: dict[str, Any] = field(default_factory=dict)
```

### 3.4 Rollout

`Rollout` 是某个 policy snapshot 在某个 case 上执行后的记录。

```python
@dataclass(slots=True)
class Rollout:
    case: Case
    messages: list[Message]
    policy_snapshot_id: str
    evaluation: RubricEvaluation | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
```

当前关键变化：`Rollout.evaluation` 是一等可选字段。

- 如果环境本身能给 reward / evaluation，`RolloutExecutor` 应直接填入 `rollout.evaluation`。
- 训练时 `TrajectoryRolloutAnalyzer` 优先沿用 `rollout.evaluation`；没有时才通过注入的 `RolloutEvaluator` 评估；再没有时用“是否抽取到 trajectory”作为 fallback evaluation。
- `pipeline.eval(...)` 不再调用 `RolloutAnalyzer`，只依赖 `RolloutExecutor` 返回的 `rollout.evaluation`；如果 eval rollout 缺 evaluation，会直接报错。

### 3.5 RubricEvaluation

```python
@dataclass(slots=True)
class RubricEvaluation:
    passed: bool
    score: float
    criterion_results: list[CriterionResult]
    feedback: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass(slots=True)
class CriterionResult:
    criterion_name: str
    passed: bool
    score: float
    feedback: list[str]
    evidence: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)
```

在 tau2 集成中：

- `passed = reward >= 1.0`
- `score = reward`
- report 展示以 `accuracy = passed_count / case_count` 为主，`average_reward` 为辅助指标。

## 4. SemanticGradient

`SemanticGradient` 是针对一个目标 experience 的语义更新信号。当前接口以 `MemoryFile` before/after 表达，而不是文本 patch 对象。

```python
class SemanticGradient(Protocol):
    @property
    def before_file(self) -> MemoryFile | None: ...

    @property
    def after_file(self) -> MemoryFile: ...

    @property
    def target_experience_name(self) -> str: ...
    @property
    def target_experience_uri(self) -> str | None: ...
    @property
    def base_version(self) -> int | None: ...
    @property
    def rationale(self) -> str: ...
    @property
    def links(self) -> list[StoredLink]: ...
    @property
    def confidence(self) -> float: ...
    @property
    def metadata(self) -> dict[str, Any]: ...
```

当前具体实现：

```python
@dataclass(slots=True)
class PatchSemanticGradient:
    before_file: MemoryFile | None
    after_file: MemoryFile
    base_version: int | None
    rationale: str
    links: list[StoredLink]
    confidence: float
    metadata: dict[str, Any] = field(default_factory=dict)
```

约定：

- `before_file is None` 表示建议新建。
- `after_file` 是建议的目标 memory file 状态。
- `links` 承载 exp→traj 的 provenance，沿用 v2 `MEMORY_FIELDS.links/backlinks` 协议；来源轨迹关系使用 `StoredLink(from_uri=exp_uri, to_uri=traj_uri, link_type="derived_from", weight=1.0)`，不再引入单独的轨迹 URI 列表字段。
- patch 文本不是 gradient 自身字段，而是由 `PatchMergeContextProvider` 在 merge 阶段把 before/after memory file 渲染为字段级 unified diff。

## 5. PolicyUpdatePlan / PolicyUpdater

`PolicyOptimizer.plan(...)` 输出 `PolicyUpdatePlan`，`PolicyUpdater.apply(...)` 负责真正写文件。

```python
PolicyPlanItemKind = Literal["upsert_experience", "delete_experience"]

@dataclass(slots=True)
class PolicyPlanItem:
    kind: PolicyPlanItemKind
    target_experience_name: str
    target_experience_uri: str | None
    before_content: str | None
    after_content: str | None
    base_version: int | None = None
    confidence: float | None = None
    links: list[StoredLink] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass(slots=True)
class PolicyUpdatePlan:
    items: list[PolicyPlanItem] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass(slots=True)
class PolicyApplyResult:
    updated_policy_set: ExperienceSet
    written_uris: list[str] = field(default_factory=list)
    deleted_uris: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
```

当前 `MemoryFilePolicyUpdater` 支持：

- `upsert_experience`
- `delete_experience`
- 基于 `before_content` 的轻量 base-content guard，避免覆盖已发散内容。

## 6. 接口定义

### 6.1 CaseLoader

```python
class CaseLoader(Protocol):
    async def batches(self, context: Any) -> AsyncIterator[list[Case]]: ...
```

实现：

- `ListCaseLoader`
- `RemoteCaseLoader`：通过 HTTP 服务拉取 cases。

### 6.2 RolloutExecutor

```python
class RolloutExecutor(Protocol):
    async def execute(
        self,
        cases: list[Case],
        policy_set: ExperienceSet,
        context: ExecutionContext,
    ) -> list[Rollout]: ...
```

实现：

- `SingleTurnLLMRolloutExecutor`
- `RemoteRolloutExecutor`
- `Tau2RolloutExecutor`（benchmark/tau2 内部实现，通过 tau2 service 暴露给训练流程）

### 6.3 RolloutEvaluator

```python
class RolloutEvaluator(Protocol):
    async def evaluate(self, rollout: Rollout, context: Any) -> RubricEvaluation: ...
```

用途：环境不能直接提供 `rollout.evaluation` 时，`RolloutAnalyzer` 可注入 evaluator 进行评估。

### 6.4 RolloutAnalyzer

```python
class RolloutAnalyzer(Protocol):
    async def analyze(self, rollout: Rollout, context: Any) -> RolloutAnalysis: ...
```

当前实现：`TrajectoryRolloutAnalyzer`。

职责：

1. 确定 rollout evaluation：
   - 优先使用 `rollout.evaluation`
   - 否则使用注入的 `RolloutEvaluator`
   - 否则基于是否抽取到 trajectory 生成默认 evaluation
2. 将 evaluation feedback 追加到 trajectory extraction messages。
3. 通过 `AgentTrajectoryContextProvider + ExtractLoop` 只抽取 `trajectories` memory type。
4. 通过 `MemoryUpdater.apply_operations(...)` 写入 trajectory memory。
5. 读取写入的 trajectory 文件并返回 `RolloutAnalysis`。

### 6.5 GradientEstimator

```python
class GradientEstimator(Protocol):
    async def estimate(
        self,
        analysis: RolloutAnalysis,
        experience_set: ExperienceSet,
        context: Any,
    ) -> list[SemanticGradient]: ...
```

当前实现：`ExperienceGradientEstimator`。

它复用：

- `AgentExperienceContextProvider`
- `ExtractLoop`
- `MemoryIsolationHandler(allowed_memory_types={"experiences"})`

但不调用 `MemoryUpdater.apply_operations(...)`。它把 ExtractLoop 产生的 upsert operations 转成 `PatchSemanticGradient`。

### 6.6 PolicyOptimizer

```python
class PolicyOptimizer(Protocol):
    async def plan(
        self,
        gradients: list[SemanticGradient],
        policy_set: ExperienceSet,
        context: Any,
    ) -> PolicyUpdatePlan: ...
```

当前实现：`PatchMergePolicyOptimizer`。

它不按 target 分组限制输出，而是把一批 gradients 一次性交给 `PatchMergeContextProvider + ExtractLoop` 进行全局 merge。LLM 可以：

- 合并多个 patch 到一个 experience。
- 把一个臃肿 patch 拆成多个 experience。
- 合并相似新文件。
- 主动输出删除操作。

### 6.7 PolicyUpdater

```python
class PolicyUpdater(Protocol):
    async def apply(
        self,
        plan: PolicyUpdatePlan,
        policy_set: ExperienceSet,
        context: Any,
    ) -> PolicyApplyResult: ...
```

实现：

- `DryRunPolicyUpdater`
- `MemoryFilePolicyUpdater`

### 6.8 PolicyTrainer

```python
class PolicyTrainer(Protocol):
    async def train_rollouts(
        self,
        rollouts: list[Rollout],
        policy_set: ExperienceSet,
        context: Any,
        analyses: list[RolloutAnalysis] | None = None,
    ) -> RolloutTrainingResult: ...
```

实现：

- `BatchPolicyTrainer`：显式 batch，本地执行 analyze/estimate/plan/apply。
- `StreamingPolicyTrainer`：实时 rollout 输入，先 analyze/estimate，再按梯度数量和时间窗口攒批，批量 plan/apply。
- `SessionCommitPolicyTrainer`：把 rollout 写入远端 OpenViking session，通过 `session.commit` 让服务端完成训练。

### 6.9 PolicyOptimizationPipeline

```python
class PolicyOptimizationPipeline(Protocol):
    async def train(...) -> PipelineResult: ...
    async def eval(...) -> PipelineEvaluationResult: ...
    async def train_from_rollouts(...) -> RolloutTrainingResult: ...
```

当前实现：`OfflinePolicyOptimizationPipeline`。

## 7. PipelineContext / ExecutionContext

```python
@dataclass(slots=True)
class PipelineContext:
    case_load_context: Any = None
    snapshot_context: Any = None
    analysis_context: Any = None
    gradient_context: Any = None
    optimization_context: Any = None
    apply_context: Any = None
    execution_metadata: dict[str, Any] = field(default_factory=dict)
    max_epochs: int = 1

@dataclass(slots=True)
class ExecutionContext:
    policy_snapshot_id: str
    metadata: dict[str, Any] = field(default_factory=dict)
```

`max_epochs` 是训练迭代次数。之前文档中的 `max_iterations` 已改为 epoch 概念。

## 8. 训练流程

### 8.1 OfflinePolicyOptimizationPipeline.train

```text
for epoch in range(ctx.max_epochs):
  for cases in case_loader.batches(...):
    snapshot_id = snapshotter.snapshot(policy_set)
    rollouts = rollout_executor.execute(cases, policy_set, ExecutionContext(snapshot_id))
    training_result = policy_trainer.train_rollouts(rollouts, policy_set, ctx)
    policy_set = training_result.apply_result.updated_policy_set
```

默认 `policy_trainer` 是 `BatchPolicyTrainer`，因此本地训练链路为：

```text
Rollout[]
  -> RolloutAnalyzer.analyze(...)
  -> GradientEstimator.estimate(...)
  -> PolicyTrainingEngine.plan_and_apply(...)
       -> async with ExperienceSet.lock()
       -> ExperienceSet.reload()
       -> PolicyOptimizer.plan(...)
       -> PolicyUpdater.apply(...)
```

### 8.2 OfflinePolicyOptimizationPipeline.eval

```text
CaseLoader -> RolloutExecutor -> Rollout.evaluation -> PipelineEvaluationResult
```

eval 阶段不会调用 `RolloutAnalyzer`，不会抽 trajectory，也不会写 policy。它要求 `RolloutExecutor` 返回带 `evaluation` 的 rollout。

### 8.3 train_from_rollouts

实时场景或外部系统已经产生 rollout 时，可以绕过 `CaseLoader / PolicySnapshotter / RolloutExecutor`：

```text
Rollout[] -> policy_trainer.train_rollouts(...)
```

约束：每个 rollout 必须包含 `case`。

## 9. Batch 与 Streaming

### 9.1 BatchPolicyTrainer

适合离线训练，输入一批 rollout 后直接完成一次：

```text
analyze -> estimate -> plan -> apply
```

### 9.2 StreamingPolicyTrainer

适合实时 commit / 并发 rollout 场景。

流程：

```text
submit_rollout(rollout)
  -> analyze rollout
  -> estimate gradients
  -> submit gradients to StreamingBatcher
  -> 等待该 rollout 所在 batch 被 flush 并 apply
```

flush 触发条件：

- `max_gradients_per_update` 达到阈值
- 最老 gradient 等待超过 `max_wait_seconds`
- `close()` 时 flush 剩余内容

默认配置：

```python
@dataclass(slots=True)
class StreamingPolicyTrainerConfig:
    max_gradients_per_update: int = 8
    max_wait_seconds: float = 10.0
    timer_check_interval_seconds: float = 1.0
    trace_console: bool = False
```

进程内全局共享：

```python
get_streaming_policy_trainer(...)
make_streaming_policy_trainer_key(policy_root_uri, request_context)
```

并发安全由 `PolicyTrainingEngine.plan_and_apply(...)` 中的 `ExperienceSet.lock()` 保证。

## 10. Patch Merge 机制

### 10.1 PatchSemanticGradient 到 PatchMergePatch

`PatchMergePolicyOptimizer` 会把每个 `SemanticGradient` 转为：

```python
@dataclass(slots=True)
class PatchMergePatch:
    before_file: MemoryFile | None
    after_file: MemoryFile
    metadata: dict[str, Any]
```

### 10.2 PatchMergeContextProvider

位置：`openviking/session/memory/patch_merge_context_provider.py`

职责：

- 给 LLM 提供待合并 patch 相关的原始 memory 文件。
- 将 `MemoryFile` before/after 渲染为字段级 unified diff。
- 用 embedding 检索额外候选文件，帮助发现相似/重复 memory。
- 暴露指定 memory type 的 schema，让 ExtractLoop 输出合法 memory operations。

输入文件选择：

```text
required_file_uris = patch target uri / superseded policy uri
extra_candidate_files = embedding search 当前 memory_type 下的相似文件
max_extra_candidate_files = max(5, len(required_file_uris))
search_limit = max_extra_candidate_files * 2
```

字段 diff 规则：

- 只展示发生变化的字段。
- 字符串按行 diff。
- dict/list 先 JSON 格式化再 diff。
- `content` 已在 `Field Diff: content` 中展示，因此不会额外在 metadata 中重复塞完整 content。

### 10.3 PatchMergePolicyOptimizer

```text
SemanticGradient[]
  -> PatchMergeContextProvider.prefetch()
  -> ExtractLoop(max_iterations=1)
  -> ResolvedOperations
  -> PolicyPlanItem[]
```

输出支持：

- upsert experience
- delete experience

merge 输入/输出日志通过 `tracer.info(..., console=False)` 记录，避免默认污染 console。

## 11. session.commit 实时训练接入

`SessionCompressorV3` 已把用户记忆抽取和实时训练接起来。

### 11.1 用户记忆抽取

`SessionCompressorV3._extract_user_memories(...)`：

1. 通过原用户记忆 `ExtractLoop` 抽取用户记忆。
2. case 不再额外单独调用 LLM，而是作为一种普通 memory type：`cases`。
3. 抽取结果交给 `StreamingMemoryUpdater` 做 patch merge 写入用户记忆。
4. 如有 `archive_uri`，写入 `memory_diff.json`，其中包含顶层 `trace_id`。

`memory_diff.json` 顶层结构包含：

```json
{
  "archive_uri": "...",
  "trace_id": "...",
  "extracted_at": "...",
  "operations": {...},
  "summary": {...}
}
```

### 11.2 从 cases 触发 streaming train

`SessionCompressorV3.train_from_extracted_cases(...)`：

```text
extracted Case[] + original commit messages
  -> Rollout(case, messages, policy_snapshot_id=session-commit:...)
  -> StreamingPolicyTrainer.submit_rollout(...)
```

即真实 session.commit 产生的对话可以被转为 rollout 输入训练框架。

## 12. SessionCommitPolicyTrainer：远程服务端训练

`SessionCommitPolicyTrainer` 是一个 `PolicyTrainer` 实现，用于“训练框架在外部，OpenViking 服务端负责训练”的场景。

它会把 rollout 写成一个临时 session：

```text
[CaseSpec message]
[Rollout messages]
[OutcomeEvaluation message]
```

其中：

- `CaseSpec` 放在开头，只含 case/rubric/task context，不含 evaluation。
- `OutcomeEvaluation` 放在最后，只含 evaluation，作为训练信号。
- rollout 的工具结果会通过 `ToolPart` 的 `tool_output` 上传，而不是普通 text。

然后执行：

```text
client.create_session(...)
client.batch_add_messages(...)
client.commit_session(...)
client.get_task(...) until completed/failed/timeout
```

CaseSpec 会做精简，避免传入巨大或重复字段：

- 不传 `policy`
- 不传 `data_root`
- 不传 `rollout_metadata`
- 不传 `policy_snapshot_id`
- 保留 `domain/split/data_split/task_id/task_no/user_query/ground_truth/rubric`

## 13. Remote HTTP 组件

`components/remote.py` 提供通用 HTTP 组件：

- `RemoteCaseLoader`
- `RemoteRolloutExecutor`

它们面向一个环境/benchmark service：

```text
POST /v1/cases/query
POST /v1/rollouts/execute
GET  /v1/rollouts/executions/{execution_id}
```

其中 `/v1/rollouts/execute` 只负责提交单个 case 的 rollout execution，返回
`execution_id`；`RemoteRolloutExecutor` 会并发提交多个 case，并通过
`/v1/rollouts/executions/{execution_id}` 轮询状态。这样长耗时 rollout 不会占用
一个超长 HTTP request，也便于未来 benchmark service 做多机部署和负载均衡。

这样训练框架不需要直接依赖 tau2 或其他 benchmark 的代码，只依赖通用
Case/Rollout JSON 协议。

## 14. tau2 集成

### 14.1 架构

当前 tau2 训练分为两个进程：

```text
tau2 service
  - 依赖 tau2 / vikingbot
  - 暴露 case query 和 rollout execute HTTP API

train/eval runner
  - 使用 RemoteCaseLoader / RemoteRolloutExecutor
  - 使用 SessionCommitPolicyTrainer 提交 OpenViking session.commit
  - 本身不直接依赖 tau2 runtime
```

### 14.2 tau2 service

位置：

```text
benchmark/tau2/train/service_app.py
benchmark/tau2/train/run_service.sh
```

启动：

```bash
benchmark/tau2/train/run_service.sh \
  --host 127.0.0.1 \
  --port 1944
```

### 14.3 remote train/eval

位置：

```text
benchmark/tau2/train/run_batch_train_eval.sh
openviking/session/train/run_batch_train_eval.py
openviking/session/train/batch_runner.py
```

预先只跑 test 分数（不训练）：

```bash
benchmark/tau2/train/run_batch_train_eval.sh \
  --epochs 0 \
  --eval-index 24 \
  --trials 8
```

训练前先跑一次 test baseline，再训练并跑最终 test：

```bash
benchmark/tau2/train/run_batch_train_eval.sh \
  --baseline-eval \
  --epochs 4 \
  --trials 8
```

输出以 accuracy 为主，阶段日志由 session/train lifecycle hooks 统一输出：

```text
[baseline_rollout] epoch=-1 trials=8 cases_per_trial=25 total_rollouts=200 accuracy=... ± ... avg_reward=... ± ...
================= epoch 0 =================
[train_rollout] epoch=0 cases=25 accuracy=... passed=... avg_reward=...
[train] epoch=0 commits=25 errors=0
[final_test_rollout] epoch=4 trials=8 cases_per_trial=25 total_rollouts=200 accuracy=... ± ... avg_reward=... ± ...
```

### 14.4 tau2 rollout messages

`Tau2RolloutExecutor` 会把工具结果转成真正的 `ToolPart`：

```json
{
  "type": "tool",
  "tool_id": "tau2-tool-0",
  "tool_name": "get_reservation_details",
  "tool_input": {...},
  "tool_output": "...",
  "tool_status": "completed"
}
```

这样上传到 `session.commit` 后，服务端可以复用已有 tool output 外部化和 memory extraction 逻辑。


## 15. tau2 接入新评测框架示意图

tau2 的接入方式体现了推荐的 benchmark 集成模式：benchmark runtime 独立成 HTTP service，训练框架只通过通用 `RemoteCaseLoader` / `RemoteRolloutExecutor` 接入。

<img src="https://gist.githubusercontent.com/chenjw/5c8f05a10f2c3f1913eb6c9d4293f0a4/raw/d9151bc8bbceccf3e56486897061c76a5d6f0cfa/tau2-train-eval-architecture.svg" alt="tau2 接入 OpenViking 新训练评测框架" width="100%">


图中需要特别注意：tau2 runtime service 虽然不负责训练写入，但它执行 rollout 时会通过 VikingBot / OpenViking tools 读取当前 OpenViking memories。因此 final_eval 能看到 train epoch 后写入的最新 experiences。

### 15.1 接入分层

```text
tau2 service
  - 依赖 tau2 / vikingbot
  - 负责 case 查询、rollout 执行、环境 reward 评估
  - 输出通用 Case / Rollout / RubricEvaluation JSON

train/eval runner
  - 不直接依赖 tau2 runtime
  - 使用 RemoteCaseLoader 查询 case
  - 使用 RemoteRolloutExecutor 执行 rollout
  - 使用 SessionCommitPolicyTrainer 把训练 rollout 提交给 OpenViking 服务端

OpenViking server
  - 通过 session.commit 接收 rollout messages
  - 服务端内部执行 trajectory extraction / gradient estimation / patch merge / policy update
```

### 15.2 train/eval 时序

```text
baseline_eval:
  RemoteCaseLoader(test)
    -> RemoteRolloutExecutor
    -> Tau2RolloutExecutor
    -> rollout.evaluation
    -> accuracy / avg_reward report

train epoch:
  RemoteCaseLoader(train)
    -> RemoteRolloutExecutor
    -> Tau2RolloutExecutor
    -> SessionCommitPolicyTrainer
    -> session.commit
    -> SessionCompressorV3
    -> StreamingPolicyTrainer
    -> experiences update

final_eval:
  RemoteCaseLoader(test)
    -> RemoteRolloutExecutor
    -> Tau2RolloutExecutor reads latest OpenViking experiences
    -> rollout.evaluation
    -> accuracy delta report
```

### 15.3 为什么 eval 不走 RolloutAnalyzer

在 tau2 场景中，环境执行完 rollout 后可以直接给出 reward，因此 `Tau2RolloutExecutor` 会返回：

```python
Rollout(
    case=case,
    messages=messages,
    policy_snapshot_id=snapshot_id,
    evaluation=RubricEvaluation(...),
)
```

所以 `OfflinePolicyOptimizationPipeline.eval(...)` 只统计 `rollout.evaluation`：

```text
accuracy = passed_count / case_count
average_reward = mean(evaluation.score)
```

eval 不抽 trajectory、不估计 gradient、不写 experience。

### 15.4 训练如何通过 session.commit 进入服务端

`SessionCommitPolicyTrainer` 会把 rollout 转成临时 session messages：

```text
[OpenViking Training CaseSpec]
[Rollout messages: user / assistant / ToolPart]
[OpenViking OutcomeEvaluation]
```

其中：

- `CaseSpec` 放在开头，只描述任务和 rubric，不包含 evaluation。
- `OutcomeEvaluation` 放在最后，作为训练信号。
- tau2 工具结果使用 `ToolPart.tool_output` 上传，服务端可以复用已有 tool output 外部化和 memory extraction 逻辑。

### 15.5 指标展示

tau2 runner 的报告以正确率为主：

```text
[baseline_eval] epoch=-1 cases=10 accuracy=20.00% passed=2/10 avg_reward=0.200000
[train_epoch] epoch=0 cases=50 accuracy=18.00% passed=9/50 avg_reward=0.180000 commits=50 errors=0
[final_eval] epoch=1 cases=10 accuracy=30.00% passed=3/10 avg_reward=0.300000

baseline accuracy: 20.00% (2/10)
final accuracy: 30.00% (3/10)
accuracy delta: +10.00pp
```

`average_reward` 保留为辅助指标；主指标是 `accuracy`。

### 15.6 以 tau2 为例：新场景接入需要实现的接口

一个新的 benchmark / domain / environment 接入训练评测框架时，推荐复用 tau2
的分层方式：把场景 runtime 独立成一个 HTTP service，训练进程继续使用通用
`RemoteCaseLoader` / `RemoteRolloutExecutor`。训练框架不关心场景内部怎么启动
agent、怎么调用工具、怎么计算 reward，只要求 service 实现下面这些协议。

#### 15.6.1 Case 查询接口

```text
POST /v1/cases/query
```

请求：

```json
{
  "dataset": "tau2",
  "domain": "airline",
  "split": "train",
  "cursor": null,
  "limit": 100,
  "filters": {}
}
```

响应：

```json
{
  "cases": [
    {
      "name": "tau2_airline_train_0",
      "task_signature": "tau2:airline:train:0",
      "input": {
        "domain": "airline",
        "split": "train",
        "task_id": "0",
        "task_no": 0,
        "user_query": "...",
        "ground_truth": "..."
      },
      "rubric": {
        "name": "tau2_airline_train_0_rubric",
        "description": "...",
        "criteria": [
          {
            "name": "tau2_reward",
            "description": "The tau2 environment reward is 1.0.",
            "required": true,
            "weight": 1.0,
            "metadata": {}
          }
        ],
        "metadata": {}
      },
      "metadata": {
        "dataset": "tau2",
        "domain": "airline",
        "source": "tau2",
        "split": "train"
      }
    }
  ],
  "next_cursor": "100"
}
```

接入要求：

- `dataset/domain/split` 用于定位数据集切片。
- `cursor/limit` 用于分页；没有下一页时 `next_cursor = null`。
- `Case.input` 只放 rollout 必需的任务输入和场景元信息，不要塞训练框架已经能从
  上下文拿到的内容，例如完整 system prompt、完整 rollout metadata、evaluation
  结果或 policy snapshot。
- `Case.rubric` 必须能描述评测目标；如果环境能直接给 reward，也仍然要提供
  rubric，便于训练侧把 reward 转成统一的 `RubricEvaluation`。

tau2 中对应实现是：

```text
benchmark/tau2/train/service_app.py::query_cases
benchmark/tau2/train/case_loader.py::Tau2CaseLoader
```

#### 15.6.2 Rollout 提交接口

```text
POST /v1/rollouts/execute
```

请求：

```json
{
  "case": { "...": "Case JSON" },
  "policy_set": {
    "root_uri": "viking://user/default/memories/experiences",
    "policies": [],
    "metadata": {}
  },
  "execution_context": {
    "policy_snapshot_id": "tau2-policy-snapshot:...",
    "metadata": {
      "epoch": 0,
      "training": true
    }
  },
  "options": {
    "config_path": "/path/to/ov.conf",
    "max_iterations": 30,
    "keep_default_tools": true,
    "rollout_language": "default"
  }
}
```

响应：

```json
{
  "execution_id": "rollout_exec_...",
  "status": "running",
  "case_name": "tau2_airline_train_0",
  "created_at": 1781097747.0,
  "updated_at": 1781097747.0,
  "error": null
}
```

接入要求：

- 该接口只提交一个 case 的 rollout execution，不需要同步等待 rollout 完成。
- 客户端会对多个 case 发起多个请求，service 端可以自行排队、限流、调度到不同
  worker 或机器。
- `policy_set.root_uri` 告诉 runtime 当前 experiences 根目录；tau2 rollout 期间
  VikingBot 会通过 OpenViking recall 读取这里的最新经验。
- `execution_context.policy_snapshot_id` 必须原样写入返回的 `Rollout.policy_snapshot_id`，
  用于追踪这次 rollout 使用的是哪次 policy snapshot。

tau2 中对应实现是：

```text
benchmark/tau2/train/service_app.py::execute_rollout
benchmark/tau2/train/service_app.py::_run_rollout_execution
benchmark/tau2/train/rollout_executor.py::Tau2RolloutExecutor
```

#### 15.6.3 Rollout 状态轮询接口

```text
GET /v1/rollouts/executions/{execution_id}
```

运行中响应：

```json
{
  "execution_id": "rollout_exec_...",
  "status": "running",
  "case_name": "tau2_airline_train_0",
  "created_at": 1781097747.0,
  "updated_at": 1781097750.0,
  "error": null
}
```

完成响应：

```json
{
  "execution_id": "rollout_exec_...",
  "status": "completed",
  "case_name": "tau2_airline_train_0",
  "created_at": 1781097747.0,
  "updated_at": 1781097760.0,
  "error": null,
  "rollout": {
    "case": { "...": "Case JSON" },
    "messages": [
      {
        "role": "user",
        "parts": [
          {
            "type": "text",
            "text": "..."
          }
        ]
      },
      {
        "role": "assistant",
        "parts": [
          {
            "type": "tool",
            "tool_id": "tau2-tool-0",
            "tool_name": "get_reservation_details",
            "tool_input": {"reservation_id": "EHGLP3"},
            "tool_output": "...",
            "tool_status": "completed"
          }
        ]
      }
    ],
    "policy_snapshot_id": "tau2-policy-snapshot:...",
    "evaluation": {
      "passed": false,
      "score": 0.0,
      "criterion_results": [
        {
          "criterion_name": "tau2_reward",
          "passed": false,
          "score": 0.0,
          "feedback": ["tau2 environment reward is below 1.0."],
          "evidence": [],
          "metadata": {"reward": 0.0}
        }
      ],
      "feedback": ["tau2 environment reward is below 1.0."],
      "metadata": {
        "source": "tau2_executor",
        "reward": 0.0
      }
    },
    "metadata": {
      "memory": "...",
      "tools_used": [],
      "iterations": 6
    }
  }
}
```

失败响应：

```json
{
  "execution_id": "rollout_exec_...",
  "status": "failed",
  "case_name": "tau2_airline_train_0",
  "created_at": 1781097747.0,
  "updated_at": 1781097752.0,
  "error": "..."
}
```

接入要求：

- `status` 至少支持 `running/completed/failed`。
- `completed` 时必须返回完整 `rollout`。
- `failed` 时必须返回可读 `error`，训练侧会把它归入该 case 的 rollout 失败。
- `Rollout.messages` 应使用 OpenViking `Message` / `Part` 结构；工具调用和工具结果
  用 `ToolPart`，不要把 `tool-call:\nname: ...` 塞进普通 text content。
- `Rollout.evaluation` 在 eval 阶段是必需字段；如果没有 evaluation，
  `OfflinePolicyOptimizationPipeline.eval(...)` 会失败。

#### 15.6.4 RolloutExecutor 内部职责

新场景自己的 rollout executor 需要完成这些事情：

1. 根据 `Case.input` 初始化环境和用户模拟器。
2. 根据 `policy_set.root_uri` / OpenViking 配置让 agent 读取当前 experiences。
3. 执行 agent loop，记录 user/assistant/tool messages。
4. 把环境 reward 或 judge 结果转成 `RubricEvaluation`。
5. 返回统一 `Rollout`：

```python
Rollout(
    case=case,
    messages=messages,
    policy_snapshot_id=context.policy_snapshot_id,
    evaluation=RubricEvaluation(...),
    metadata={
        "tools_used": [...],
        "iterations": ...,
        "memory": "...",
    },
)
```

tau2 的 `Tau2RolloutExecutor` 就是这个适配层：它一侧依赖 tau2/VikingBot runtime，
另一侧只输出训练框架理解的 `Rollout`。

#### 15.6.5 最小接入清单

接入一个新场景，最少需要实现：

| 接口/组件 | 必需 | 作用 |
|---|---:|---|
| `POST /v1/cases/query` | 是 | 分页返回 `Case[]` |
| `POST /v1/rollouts/execute` | 是 | 提交单个 rollout execution |
| `GET /v1/rollouts/executions/{execution_id}` | 是 | 轮询 rollout 状态并取回 `Rollout` |
| `RubricEvaluation` 转换 | eval 必需 | 把场景 reward/judge 结果转成统一 evaluation |
| `Message` / `ToolPart` 转换 | 训练必需 | 保留 agent 行为和工具证据，供 session.commit 抽取 trajectory/experience |
| `GET /health` | 建议 | 方便 runner 或部署系统做 preflight |

如果新场景不想提供 HTTP service，也可以在同进程内直接实现
`CaseLoader` / `RolloutExecutor` Protocol；但跨进程、多机或重 runtime 依赖的场景，
推荐采用 tau2 这种 service 方式。

## 16. 当前主要组件清单

| 组件 | 文件 | 说明 |
|---|---|---|
| `OfflinePolicyOptimizationPipeline` | `pipeline.py` | 离线 train/eval 编排 |
| `PolicyTrainingEngine` | `engine.py` | 共享 analyze/estimate/plan/apply 内核 |
| `ListCaseLoader` | `components/case_loader.py` | 内存 case loader |
| `RemoteCaseLoader` | `components/remote.py` | HTTP case loader |
| `RemoteRolloutExecutor` | `components/remote.py` | HTTP rollout executor |
| `SingleTurnLLMRolloutExecutor` | `components/rollout_executor.py` | 简单单轮 LLM rollout |
| `TrajectoryRolloutAnalyzer` | `components/trajectory_analyzer.py` | 抽取 trajectory memory |
| `ExperienceGradientEstimator` | `components/gradient_estimator.py` | trajectory -> PatchSemanticGradient |
| `PatchMergePolicyOptimizer` | `components/policy_optimizer.py` | 多 gradient 全局 merge |
| `DryRunPolicyUpdater` | `components/policy_updater.py` | dry-run apply |
| `MemoryFilePolicyUpdater` | `components/policy_updater.py` | VikingFS 写回 experiences |
| `BatchPolicyTrainer` | `components/policy_trainer.py` | batch rollout 训练 |
| `StreamingPolicyTrainer` | `components/policy_trainer.py` | 实时攒批训练 |
| `SessionCommitPolicyTrainer` | `components/session_commit.py` | 通过 session.commit 远程训练 |
| `ContentHashPolicySnapshotter` | `components/snapshotter.py` | 内容 hash snapshot id |
| `ExperienceSetLoader` | `components/memory_store.py` | 从 experiences 目录加载 policy set |

## 17. 端到端本地训练伪代码

```python
policy_set = await ExperienceSetLoader(viking_fs).load(
    "viking://user/default/memories/experiences",
    ctx=request_context,
)

pipeline = OfflinePolicyOptimizationPipeline(
    snapshotter=ContentHashPolicySnapshotter(),
    rollout_executor=SomeRolloutExecutor(),
    rollout_analyzer=TrajectoryRolloutAnalyzer(viking_fs=viking_fs, vikingdb=vikingdb),
    gradient_estimator=ExperienceGradientEstimator(viking_fs=viking_fs),
    policy_optimizer=PatchMergePolicyOptimizer(viking_fs=viking_fs),
    policy_updater=MemoryFilePolicyUpdater(viking_fs=viking_fs),
)

result = await pipeline.train(
    case_loader=ListCaseLoader(cases, batch_size=8),
    policy_set=policy_set,
    context=PipelineContext(
        max_epochs=1,
        analysis_context=TrajectoryAnalyzerContext(request_context=request_context),
        gradient_context=ExperienceGradientContext(
            request_context=request_context,
            messages=[],
        ),
        optimization_context=PatchMergePolicyOptimizerContext(
            request_context=request_context,
        ),
        apply_context=request_context,
    ),
)
```

## 18. 设计原则

- `Case` 是训练/评测样本，不再使用 `Outcome` 概念。
- `Rubric` 定义验收标准；`RubricEvaluation` 是一次 rollout 的评估结果。
- `Rollout` 保留原始执行消息和可选 evaluation；`Trajectory` 是从 rollout 中抽取的可训练样本。
- `SemanticGradient` 是 memory-file before/after 级别的语义更新信号。
- `PolicyOptimizer` 只规划，不写文件；`PolicyUpdater` 才是写入边界。
- batch 和 streaming 共用同一个 `PolicyTrainingEngine`。
- 并发写入通过 `ExperienceSet.lock() + reload()` 串行化 optimizer/apply 阶段。
- 远程 benchmark 集成应走 `RemoteCaseLoader / RemoteRolloutExecutor`，不要让训练框架直接依赖 benchmark runtime。
