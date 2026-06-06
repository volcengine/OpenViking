# Trajectory / Experience 经验优化 Domain Model 与接口设计

> 本文档用于重新定义 `trajectories` / `experiences` 经验优化模块的 domain model 与核心接口。
>
> 本文档记录 `openviking.session.train` 新训练框架的 domain model 与核心接口。该框架与现有 trajectory/experience 抽取链路并行实现，完成后再逐步替换旧框架。

## 1. Policy

`Policy` 是从 trajectories 中优化得到的可复用执行策略接口。

在当前 `trajectories` / `experiences` 经验优化模块中，`Experience` 是 `Policy` 的具体实现，对应 experiences 目录下的单个 experience 文件：

```text
viking://user/<user>/memories/experiences/<experience_name>.md
```

### 1.1 Policy 接口

```python
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


PolicyStatus = Literal["draft", "staging", "production", "deprecated", "archived"]


class Policy(Protocol):
    """A reusable execution policy optimized from trajectories."""

    @property
    def name(self) -> str:
        ...

    @property
    def uri(self) -> str:
        ...

    @property
    def version(self) -> int:
        ...

    @property
    def status(self) -> PolicyStatus:
        ...

    @property
    def content(self) -> str:
        ...

    @property
    def metadata(self) -> dict[str, Any]:
        ...
```

### 1.2 Experience 数据模型

```python
@dataclass
class Experience:
    name: str
    uri: str
    version: int
    status: PolicyStatus
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
```

### 1.3 设计约定

- `uri` 是 policy 的唯一定位符。
- `name` 对应当前 experience 文件中的 `experience_name`。
- `policy_id` 不作为强约束字段；如果未来需要跨 rename 的稳定身份，可放入 `metadata["stable_id"]`。
- `metadata` 用于承载扩展信息，例如 task signature、lineage、source gradients、created_at、updated_at 等。

## 2. ExperienceSet

`ExperienceSet` 是 experiences 目录下所有 `Experience` 的集合。

```text
viking://user/<user>/memories/experiences/
```

该目录中的所有 experience 文件共同构成当前用户 / agent 的经验策略集合。

### 2.1 数据模型

```python
@dataclass
class ExperienceSet:
    root_uri: str
    policies: list[Experience]
    metadata: dict[str, Any] = field(default_factory=dict)
```

### 2.2 设计约定

- `root_uri` 是 experiences 目录 URI。
- `policies` 是该目录下所有 experience 文件解析后的快照。
- `PolicyOptimizer` 以整个 `ExperienceSet` 为优化对象，而不是只优化单个 experience 文件。

## 3. Trajectory

`Trajectory` 是从单个 trajectory 文件解析出的 agent 执行轨迹样本。

对应当前 trajectories 目录下的单个文件：

```text
viking://user/<user>/memories/trajectories/<trajectory_name>_<timestamp>.md
```

### 3.1 数据模型

```python
@dataclass
class Trajectory:
    name: str
    uri: str
    content: str
    outcome: str
    retrieval_anchor: str
    metadata: dict[str, Any] = field(default_factory=dict)
```

### 3.2 设计约定

- `uri` 是 trajectory 文件的唯一定位符。
- `name` 对应当前 trajectory 文件中的 `trajectory_name`。
- `outcome` 先沿用当前 trajectory schema 中的字符串：`success`、`failure`、`partial`、`unfinished`、`unknown`。
- `retrieval_anchor` 沿用现有 trajectory schema，用于语义检索与分组。
- 如果未来需要区分原始执行日志与抽取后的轨迹样本，可新增 `RawTrace`；当前 `Trajectory` 表示已抽取、可用于经验优化的轨迹样本。

## 4. SemanticGradient

`SemanticGradient` 是针对某个目标 `Experience` 的语义更新信号接口。

它表达：

```text
某个 Experience 应该如何变得更好。
```

它不直接决定最终是否创建、更新、替换、拆分、合并或删除 experience 文件；这些 policy-level 决策由 `PolicyOptimizer` 基于一批 gradients 和整个 `ExperienceSet` 统一规划。

### 4.1 SemanticGradient 接口

```python
from typing import Any, Protocol


class SemanticGradient(Protocol):
    """A semantic update signal for one target Experience."""

    @property
    def target_experience_name(self) -> str:
        ...

    @property
    def target_experience_uri(self) -> str | None:
        ...

    @property
    def base_version(self) -> int | None:
        ...

    @property
    def rationale(self) -> str:
        ...

    @property
    def evidence_trajectory_uris(self) -> list[str]:
        ...

    @property
    def confidence(self) -> float:
        ...

    @property
    def metadata(self) -> dict[str, Any]:
        ...
```

### 4.2 PatchSemanticGradient

`PatchSemanticGradient` 是 `SemanticGradient` 的一种实现，用于表达基于内容 before/after patch 的语义更新信号。

```python
@dataclass
class ExperienceContentPatch:
    before_content: str | None
    after_content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PatchSemanticGradient:
    target_experience_name: str
    target_experience_uri: str | None
    base_version: int | None
    patch: ExperienceContentPatch
    rationale: str
    evidence_trajectory_uris: list[str]
    confidence: float
    metadata: dict[str, Any] = field(default_factory=dict)
```

`before_content` 为 `None` 表示建议创建新的 experience；非空时表示基于旧内容生成更新建议。`after_content` 是建议的新 experience 正文。

### 4.3 设计约定

- 单条 `SemanticGradient` 面向一个逻辑目标 `Experience`。
- `target_experience_name` 表达逻辑目标名称。
- `target_experience_uri` 可为空；为空时表示该 gradient 指向一个建议的新 experience 或尚未解析到真实文件的逻辑目标。
- `base_version` 可为空；如果 gradient 基于某个已有 experience 版本生成，应填写该版本。
- 多个 `SemanticGradient` 可能指向同一个 `Experience`，也可能并发产生相似的新 experience 目标。
- 这些重复、冲突、拆分、合并和版本 rebase 问题由 `PolicyOptimizer` 处理。

## 5. PolicyOptimizer

`PolicyOptimizer` 接收一批 `SemanticGradient`，基于整个 `ExperienceSet` 生成 `PolicyUpdatePlan`。

它只负责规划，不直接修改文件。

```text
SemanticGradient[]
  ↓
PolicyOptimizer.plan(...)
  ↓
PolicyUpdatePlan
```

### 5.1 PolicyOptimizer 接口

```python
from typing import Protocol


class PolicyOptimizer(Protocol):
    """Plans policy-set updates from semantic gradients."""

    async def plan(
        self,
        gradients: list[SemanticGradient],
        policy_set: ExperienceSet,
        context: "OptimizationContext",
    ) -> "PolicyUpdatePlan":
        ...
```

### 5.2 职责

`PolicyOptimizer` 在整个 `ExperienceSet` 层面规划更新，负责：

- 合并指向同一 `Experience` 的 gradients。
- 合并并发产生的相似新 experience 目标。
- 处理基于过期 `base_version` 产生的 gradients。
- 发现并标记冲突 gradients。
- 发现臃肿 experience，并规划拆分。
- 发现重复 experience，并规划合并。
- 生成全局 `PolicyUpdatePlan`。

### 5.3 设计约定

- `PolicyOptimizer.plan(...)` 不写文件、不修改 `ExperienceSet`。
- `PolicyOptimizer.plan(...)` 输出的是计划，不是最终文件级 diff。
- 具体如何实施计划由 `PolicyUpdater.apply(...)` 负责。

## 6. PolicyUpdatePlan

`PolicyUpdatePlan` 是 `PolicyOptimizer` 对整个 `ExperienceSet` 生成的计划更新。

它描述“应该如何更新 policy set”，但不负责实施。

### 6.1 PolicyUpdatePlan 数据模型

```python
@dataclass
class PolicyPlanItem:
    kind: Literal["upsert_experience", "delete_experience", "review_required"]
    target_experience_name: str
    target_experience_uri: str | None
    before_content: str | None
    after_content: str | None
    base_version: int | None = None
    confidence: float | None = None
    evidence_trajectory_uris: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PolicyUpdatePlan:
    items: list[PolicyPlanItem] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
```

### 6.2 设计约定

- `items` 是 `PolicyUpdater` 可执行的计划项，第一版支持 `upsert_experience`。
- `metadata` 承载 optimizer 诊断信息，例如 groups、unresolved、conflicts。
- `PolicyPlanItem.before_content` / `after_content` 对应 patch semantic gradient 的原文件内容 / 新文件内容；`before_content=None` 表示新建。
- 本设计暂不定义独立 `PolicyUpdate` 接口。

## 7. PolicyUpdater

`PolicyUpdater` 负责实施 `PolicyUpdatePlan`，真正修改 `ExperienceSet` 对应的 experience 文件集合。

```text
PolicyUpdatePlan
  ↓
PolicyUpdater.apply(...)
  ↓
ApplyResult
```

### 7.1 PolicyUpdater 接口

```python
class PolicyUpdater(Protocol):
    """Applies a policy update plan to an ExperienceSet."""

    async def apply(
        self,
        plan: PolicyUpdatePlan,
        policy_set: ExperienceSet,
        context: "ApplyContext",
    ) -> "ApplyResult":
        ...
```

### 7.2 设计约定

- `PolicyUpdater.apply(...)` 是真正执行更新的边界。
- `PolicyUpdater` 可以有多种实现，例如 patch-based updater、rewrite-based updater、transactional file updater、human-approved updater。
- `PolicyOptimizer` 与 `PolicyUpdater` 分离，保证计划可审查、可 dry-run、可评估、可事务化执行。

### 7.3 ApplyResult 数据模型

```python
@dataclass
class ApplyResult:
    updated_policy_set: ExperienceSet
    written_uris: list[str] = field(default_factory=list)
    deleted_uris: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
```

### 7.4 ApplyResult 设计约定

- `updated_policy_set` 是 apply 后的 `ExperienceSet` 快照。
- `written_uris` 记录新建或修改的 experience 文件。
- `deleted_uris` 记录删除或 deprecated 的 experience 文件。
- `errors` 记录执行失败信息。
- `metadata` 承载扩展信息。

### 7.5 调用链

```python
plan = await optimizer.plan(
    gradients=gradients,
    policy_set=policy_set,
    context=optimization_context,
)

result = await updater.apply(
    plan=plan,
    policy_set=policy_set,
    context=apply_context,
)
```

## 8. Case

`Case` 是一条可执行、可复现、可评估的训练/评测样例。

它用于驱动 agent loop 产生 rollout / trajectory：

```text
Policy + executor execute Case
  ↓
Rollout
  ↓
Trajectory
```

### 8.1 Case 数据模型

```python
@dataclass
class Case:
    name: str
    task_signature: str
    input: dict[str, Any]
    rubric: "Rubric"
    metadata: dict[str, Any] = field(default_factory=dict)
```

### 8.2 设计约定

- `Case` 是 case 库中的基本样例实体。
- `task_signature` 表示该 case 代表的任务类型 / intent 聚类标识。
- `input` 包含用户请求、初始上下文、环境配置等 agent loop 所需输入。
- `rubric` 是该 case 的验收标准与评分规则。

## 9. Rubric

`Rubric` 是 `Case` 的验收标准与评分规则。

它同时表达：

```text
什么叫做好 + 怎么检查是否做好
```

### 9.1 Rubric 数据模型

```python
@dataclass
class Rubric:
    name: str
    description: str
    criteria: list["RubricCriterion"]
    metadata: dict[str, Any] = field(default_factory=dict)
```

### 9.2 RubricCriterion 数据模型

```python
@dataclass
class RubricCriterion:
    name: str
    description: str
    required: bool
    weight: float
    metadata: dict[str, Any] = field(default_factory=dict)
```

### 9.3 设计约定

- `Rubric.description` 描述总体目标，即这个 case 什么结果算好。
- `Rubric.criteria` 描述具体检查标准，即如何判断是否做好。
- `required=True` 的 criterion 是 hard gate；失败时整体不通过。
- `weight` 用于非 hard-gate criteria 的评分聚合。
- 本设计不再保留独立 `Outcome` 概念；`Rubric` 统一承载验收目标与检查规则。

## 10. Rollout

`Rollout` 是某个 policy snapshot 在某个 `Case` 上执行 agent loop 后产生的一次执行记录。

当前最小定义由三部分组成：

```text
Rollout = Case + messages + policy_snapshot_id
```

### 10.1 Rollout 数据模型

```python
@dataclass
class Rollout:
    case: Case
    messages: list["Message"]
    policy_snapshot_id: str
```

### 10.2 设计约定

- `case` 是本次执行的训练/评测样例。
- `messages` 是 agent loop 产生的完整消息序列，包括 user / assistant message、tool call、tool result 等。
- `policy_snapshot_id` 指向本次执行使用的 `ExperienceSet` 快照。
- `Rollout` 是原始执行记录；`Trajectory` 是从 `Rollout.messages` 中抽取出的可训练轨迹样本。

```text
Case + ExperienceSet snapshot
  ↓ RolloutExecutor
Rollout
  ↓ RolloutAnalyzer
Trajectory
```

## 11. RolloutAnalyzer

`RolloutAnalyzer` 负责分析一次 `Rollout`，并在同一次分析中完成：

- 基于 `Case.rubric` 的评估。
- 从 `Rollout.messages` 中抽取可训练的 `Trajectory`。

这样可以避免 evaluation 和 trajectory extraction 分成两次 LLM 调用导致的上下文重复、成本增加和证据不一致。

### 11.1 RolloutAnalyzer 接口

```python
class RolloutAnalyzer(Protocol):
    """Analyzes a rollout and extracts learning signals."""

    async def analyze(
        self,
        rollout: Rollout,
        context: "AnalysisContext",
    ) -> "RolloutAnalysis":
        ...
```

### 11.2 RolloutAnalysis 数据模型

```python
@dataclass
class RolloutAnalysis:
    evaluation: "RubricEvaluation"
    trajectories: list[Trajectory]
    metadata: dict[str, Any] = field(default_factory=dict)
```

### 11.3 设计约定

- `evaluation` 是对该 rollout 是否满足 `Case.rubric` 的评估结果。
- `trajectories` 是从该 rollout 中抽取出的可训练轨迹样本。
- `RubricEvaluation` 与 `Trajectory` 是两个独立 domain model，但可以由同一次 `RolloutAnalyzer.analyze(...)` 调用产生。
- `Rollout` 是原始执行记录；`RolloutAnalysis` 是结构化分析结果。

```text
Rollout
  ↓ RolloutAnalyzer.analyze(...)
RolloutAnalysis
  ├── RubricEvaluation
  └── Trajectory[]
```

## 12. RubricEvaluation

`RubricEvaluation` 是一次 rollout 针对 `Rubric` 的结构化评估结果。

### 12.1 RubricEvaluation 数据模型

```python
@dataclass
class RubricEvaluation:
    passed: bool
    score: float
    criterion_results: list["CriterionResult"]
    feedback: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)
```

### 12.2 CriterionResult 数据模型

```python
@dataclass
class CriterionResult:
    criterion_name: str
    passed: bool
    score: float
    feedback: list[str]
    evidence: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)
```

### 12.3 设计约定

- `passed` 表示 hard-gate criteria 是否全部通过，以及整体是否通过。
- `score` 是 rubric 评分的聚合结果。
- `criterion_results` 记录每条 criterion 的检查结果。
- `feedback` 用于后续 trajectory 提取、semantic gradient 生成或人工复盘。

## 13. GradientEstimator

`GradientEstimator` 根据 `RolloutAnalysis` 和当前 `ExperienceSet` 估计 `SemanticGradient`。

机器学习类比：

```text
sample / batch + params → gradient estimate
```

在本设计中：

```text
RolloutAnalysis + ExperienceSet → SemanticGradient[]
```

### 13.1 GradientEstimator 接口

```python
class GradientEstimator(Protocol):
    """Estimates semantic gradients from rollout analysis."""

    async def estimate(
        self,
        analysis: RolloutAnalysis,
        experience_set: ExperienceSet,
        context: "GradientContext",
    ) -> list[SemanticGradient]:
        ...
```

### 13.2 设计约定

- `GradientEstimator` 不直接修改 `ExperienceSet`。
- `GradientEstimator` 不生成最终文件级 update plan。
- `GradientEstimator` 只负责从 `RolloutAnalysis` 中估计针对单个目标 `Experience` 的 `SemanticGradient`。
- 一次 `estimate(...)` 可以产生多条 gradients；每条 gradient 面向一个逻辑目标 `Experience`。

整体链路：

```text
Rollout
  ↓ RolloutAnalyzer
RolloutAnalysis
  ↓ GradientEstimator
SemanticGradient[]
  ↓ PolicyOptimizer.plan(...)
PolicyUpdatePlan
  ↓ PolicyUpdater.apply(...)
ApplyResult
```

## 14. PolicyOptimizationPipeline

`PolicyOptimizationPipeline` 是经验策略优化的顶层编排接口。

它将 case 执行、rollout 分析、gradient 估计、policy plan 生成和 policy 更新串联起来。

### 14.1 PolicyOptimizationPipeline 接口

```python
class PolicyOptimizationPipeline(Protocol):
    """Runs end-to-end policy optimization over a batch of cases."""

    async def run(
        self,
        case_loader: "CaseLoader",
        policy_set: ExperienceSet,
        context: "PipelineContext",
    ) -> "PipelineResult":
        ...
```

### 14.2 PipelineResult 数据模型

```python
@dataclass
class PipelineResult:
    analyses: list[RolloutAnalysis]
    gradients: list[SemanticGradient]
    plan: PolicyUpdatePlan
    apply_result: ApplyResult
    iterations: list[PipelineIterationResult] = field(default_factory=list)
    evaluation_passes: list[PipelineEvaluationResult] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineIterationResult:
    iteration: int
    analyses: list[RolloutAnalysis]
    gradients: list[SemanticGradient]
    plan: PolicyUpdatePlan
    apply_result: ApplyResult
    policy_snapshot_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineEvaluationResult:
    iteration: int
    analyses: list[RolloutAnalysis]
    policy_snapshot_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
```

### 14.3 端到端流程

```text
CaseLoader.batches(...)
  ↓
Case[]
  ↓ RolloutExecutor
Rollout[]
  ↓ RolloutAnalyzer
RolloutAnalysis[]
  ↓ GradientEstimator
SemanticGradient[]
  ↓ PolicyOptimizer.plan(...)
PolicyUpdatePlan
  ↓ PolicyUpdater.apply(...)
ApplyResult
```

pipeline 原生支持多轮离线迭代。每一轮都是同一套链路，只是下一轮使用上一轮
`ApplyResult.updated_policy_set`：

```text
for iteration in range(max_iterations):
  current_policy
    ↓ rollout
  Rollout[]
    ↓ evaluate + extract trajectory
  RolloutAnalysis[]
    ↓ estimate gradients
  SemanticGradient[]
    ↓ plan/apply
  updated_policy

if final_evaluation:
  updated_policy
    ↓ rollout
  Rollout[]
    ↓ evaluate only
  PipelineEvaluationResult
```

因此 `rollout -> evaluation -> train -> rollout -> evaluation` 不是测试里的特殊
手写流程，而是 `PolicyOptimizationPipeline` 的一等能力。单轮训练是
`max_iterations=1` 的特例；多轮训练通过 `PipelineContext.max_iterations` 控制。

`PipelineContext` 中与迭代相关的字段：

```python
@dataclass
class PipelineContext:
    max_iterations: int = 1
    final_evaluation: bool = False
    # 其余 context 字段分别透传给 case load / snapshot / analysis /
    # gradient / optimizer / updater 实现。
```

### 14.4 设计约定

- `PolicyOptimizationPipeline` 是编排层，不应把各阶段逻辑写死在一个大函数中。
- case 执行、rollout 分析、gradient 估计、policy plan、policy update 都应可以替换实现。
- pipeline 以 case batch 为基本执行单位。
- pipeline 负责 batch 内并发调度；`RolloutAnalyzer` 等单条处理接口不需要暴露 batch 方法。

在每个 case batch 执行前，pipeline 应先为当前 `ExperienceSet` 创建 snapshot：

```text
ExperienceSet
  ↓ PolicySnapshotter.snapshot(...)
policy_snapshot_id
  ↓ RolloutExecutor.execute(...) via ExecutionContext
Rollout[]
```
- batch mode 和 incremental mode 复用同一套 pipeline 抽象；incremental 可以看作小 batch 或单 batch。
- 第一阶段可以用同步 / 单进程实现。

## 15. CaseLoader

`CaseLoader` 是一次 policy optimization run 的 case 数据加载接口。

它负责提供 case batch，但不负责 case 的长期存储与版本管理；长期存储可以由未来的 `CaseRepository` 承担。

### 15.1 CaseLoader 接口

```python
from collections.abc import AsyncIterator
from typing import Protocol


class CaseLoader(Protocol):
    """Loads case batches for policy optimization."""

    async def batches(
        self,
        context: "CaseLoadContext",
    ) -> AsyncIterator[list[Case]]:
        ...
```

### 15.2 设计约定

- `CaseLoader` 以 batch 形式提供 `Case`。
- batch 大小、过滤条件、shuffle、train/eval split 等由 `CaseLoadContext` 或具体实现决定。
- batch mode 和 incremental mode 统一通过 `CaseLoader.batches(...)` 表达。
- incremental mode 可以返回一个小 batch 或单个 batch。
- 第一版可以提供简单的 `ListCaseLoader`，直接包装 `list[Case]`。

示例：

```python
class ListCaseLoader:
    def __init__(self, cases: list[Case]):
        self.cases = cases

    async def batches(
        self,
        context: "CaseLoadContext",
    ) -> AsyncIterator[list[Case]]:
        yield self.cases
```

## 16. RolloutExecutor

`RolloutExecutor` 给定一批 `Case` 和当前 `ExperienceSet`，执行 policy 并产生 `Rollout`。

它不绑定具体 agent loop 实现；内部可以是 agent loop、simulator、replay executor 或 mock executor。

### 16.1 RolloutExecutor 接口

```python
class RolloutExecutor(Protocol):
    """Executes cases against a policy set and produces rollouts."""

    async def execute(
        self,
        cases: list[Case],
        policy_set: ExperienceSet,
        context: "ExecutionContext",
    ) -> list[Rollout]:
        ...
```

### 16.2 设计约定

- `RolloutExecutor` 输入一个 case batch 和当前 `ExperienceSet`。
- `RolloutExecutor` 输出与该 batch 对应的一组 `Rollout`。
- 每个 `Rollout` 应记录本次执行使用的 `policy_snapshot_id`。
- `policy_snapshot_id` 由 `PolicyOptimizationPipeline` 通过 `PolicySnapshotter` 生成，并通过 `ExecutionContext` 传入 `RolloutExecutor`。
- `RolloutExecutor` 不负责生成 policy snapshot。
- `RolloutExecutor` 不负责分析 rollout，也不负责生成 trajectory 或 semantic gradient。

### 16.3 ExecutionContext 数据模型

```python
@dataclass
class ExecutionContext:
    policy_snapshot_id: str
    metadata: dict[str, Any] = field(default_factory=dict)
```

### 16.4 ExecutionContext 设计约定

- `policy_snapshot_id` 是本次 case batch 执行使用的 policy snapshot。
- `metadata` 可承载 runner 配置、模型配置、环境配置、seed 等执行上下文信息。

## 17. PolicySnapshotter

`PolicySnapshotter` 为当前 `ExperienceSet` 创建或解析一个可复现的 `policy_snapshot_id`。

该 snapshot id 用于标记 rollout 执行时使用的 policy set 版本。

### 17.1 PolicySnapshotter 接口

```python
class PolicySnapshotter(Protocol):
    """Creates a snapshot identifier for an ExperienceSet."""

    async def snapshot(
        self,
        policy_set: ExperienceSet,
        context: "SnapshotContext",
    ) -> str:
        ...
```

### 17.2 设计约定

- `snapshot(...)` 返回的字符串写入 `Rollout.policy_snapshot_id`。
- `policy_snapshot_id` 应能定位或复现 rollout 执行时使用的 `ExperienceSet`。
- snapshot 可以实现为 content hash、version id、train run id、manifest URI 等。
- 第一版只要求返回 `str`，不强制 snapshot 的存储格式。

## 18. Context Placeholders

以下 Context 类型暂作为各阶段扩展上下文占位，本文档暂不定义其内部结构：

- `OptimizationContext`
- `ApplyContext`
- `AnalysisContext`
- `GradientContext`
- `PipelineContext`
- `CaseLoadContext`
- `SnapshotContext`

### 18.1 设计约定

- Context 用于承载运行时配置、依赖对象、trace id、并发参数、模型配置、环境配置等。
- 各 Context 的字段后续按具体实现需要再定义。
- 在 domain model 稳定前，不为 Context 提前引入过多强约束字段。

## 19. Training Loop Pseudocode

以下伪代码展示 `PolicyOptimizationPipeline` 如何编排已定义接口。

```python
async for cases in case_loader.batches(case_load_context):
    snapshot_id = await snapshotter.snapshot(
        policy_set=policy_set,
        context=snapshot_context,
    )

    rollouts = await rollout_executor.execute(
        cases=cases,
        policy_set=policy_set,
        context=ExecutionContext(policy_snapshot_id=snapshot_id),
    )

    analyses = await gather(
        rollout_analyzer.analyze(
            rollout=rollout,
            context=analysis_context,
        )
        for rollout in rollouts
    )

    gradient_batches = await gather(
        gradient_estimator.estimate(
            analysis=analysis,
            experience_set=policy_set,
            context=gradient_context,
        )
        for analysis in analyses
    )
    gradients = [gradient for batch in gradient_batches for gradient in batch]

    plan = await policy_optimizer.plan(
        gradients=gradients,
        policy_set=policy_set,
        context=optimization_context,
    )

    apply_result = await policy_updater.apply(
        plan=plan,
        policy_set=policy_set,
        context=apply_context,
    )

    policy_set = apply_result.updated_policy_set
```

### 19.1 设计约定

- pipeline 负责 batch 内并发，例如并发分析多个 rollout、并发估计多个 gradient batch。
- `PolicySnapshotter.snapshot(...)` 在每个 case batch 执行前调用，保证 rollout 可追溯到执行时的 policy set。
- `PolicyOptimizer.plan(...)` 只生成计划，不修改文件。
- `PolicyUpdater.apply(...)` 是真正修改 experiences 文件集合的边界。
- 每个 batch apply 后，下一批 case 使用更新后的 `policy_set`。


## 20. Initial Adapter Implementations

第一阶段实现位于 `openviking/session/train`，不修改旧的 trajectory / experience 抽取链路。

已实现的 adapter / helper：

- `ExperienceSetLoader`：从现有 experiences 目录读取 `.md` 记忆文件，并通过 `MemoryFileUtils` 转换为 `ExperienceSet`。
- `ContentHashPolicySnapshotter`：基于 `ExperienceSet` 内容生成确定性的 `policy_snapshot_id`。
- `GroupingPolicyOptimizer`：将 `SemanticGradient` 按目标 experience 分组，输出包含 `PolicyPlanItem` 的 `PolicyUpdatePlan`，并在 metadata 中记录 groups / unresolved / conflicts。
- `DryRunPolicyUpdater`：dry-run updater，不写文件；当 plan 有 items 时会模拟生成更新后的 `ExperienceSet`，便于离线审查。
- `MemoryFilePolicyUpdater`：写回型 updater，消费 `upsert_experience` 计划项，通过 `MemoryFileUtils.write(...)` 序列化并写入 VikingFS。
- `DefaultPolicyOptimizationPipeline`：编排 `CaseLoader`、`PolicySnapshotter`、`RolloutExecutor`、`RolloutAnalyzer`、`GradientEstimator`、`PolicyOptimizer` 和 `PolicyUpdater`。
- `SingleTurnLLMRolloutExecutor`：最小可用的单轮 LLM rollout executor。它把 `ExperienceSet`、`Case.input` 和 `Case.rubric` 组装成 prompt，调用一次 LLM，返回包含 user / assistant 两条消息的 `Rollout`。后续完整 agent loop 只需要实现同一个 `RolloutExecutor` 接口即可替换。

后续 adapter 计划：

- `LegacyTrajectoryRolloutAnalyzer`：通过旧 `SessionCompressorV2.extract_agent_memories(..., allowed_memory_types={"trajectories"})` 只运行 trajectory phase，不触发旧 experience consolidation。
- `LegacyExperienceGradientEstimator`：复用旧 experience phase 的候选检索与 prompt 思路，将旧 memory operations 转换为 `PatchSemanticGradient`。
- 后续可继续增强 `PolicyOptimizer`，在 `PolicyPlanItem` 层做多 gradient 合并、相似新文件合并、冲突 rebase、臃肿 experience 拆分等。
