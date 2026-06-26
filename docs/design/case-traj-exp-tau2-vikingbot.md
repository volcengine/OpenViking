# Case / Trajectory / Experience 关系与 tau2 VikingBot 使用方式

> 目标：说明当前代码里 `case`、`traj/trajectory`、`exp/experience` 的职责、生成/链接关系，以及 tau2 的 VikingBot rollout 如何在执行时消费 experience。本文以当前仓库实现为准，并补一个端到端示例。

## 1. 一句话结论

- **Case**：可执行、可评估的题目/场景，是训练和评测的输入。
- **Trajectory**：一次 rollout 后，从对话、工具调用、reward/evaluation 中抽出的“这次执行发生了什么、成败差异是什么”的诊断样本。
- **Experience**：从 trajectory 中蒸馏出的可复用执行策略/ guardrail，是未来 agent 真正拿来指导行动的经验。
- **关系链路**：`Case -> Rollout -> Trajectory -> Experience`，再通过 memory link 回连成 `Case -> Trajectory`、`Trajectory -> Experience`、`Case -> Experience`。
- **tau2 VikingBot 使用 exp 的方式**：执行新 tau2 case 时，不直接把全部 experiences 注入 prompt，而是强制加载 `experience_loader` skill；agent 先用 `search_experience` 搜索 case memory，再从匹配 case 的 `## Linked Experiences` 里拿候选 exp URI，经过 `## Situation` gate 后再用 `read_experience` 读取并应用。

## 2. 三类对象的数据模型

### 2.1 Case：题目/训练样本

代码模型在 `openviking/session/train/domain.py`：

```python
@dataclass(slots=True)
class Case:
    name: str
    task_signature: str
    input: dict[str, Any]
    rubric: Rubric
    metadata: dict[str, Any] = field(default_factory=dict)
```

在 tau2 中，`benchmark/tau2/train/case_loader.py` 的 `Tau2CaseLoader._case_from_task(...)` 把 tau2 split task 转为 Case：

- `name`: `tau2_<domain>_<split>_<task_no>`，例如 `tau2_airline_train_14`
- `task_signature`: `tau2:<domain>:<split>:<task_id>`，例如 `tau2:airline:train:21`
- `input`: 包含 `domain`、`split`、`data_split`、`task_no`、`task_id`、`user_query` 等
- `rubric`: tau2 reward 必须达到 `1.0`

在 session commit 训练路径里，Case 也会被写入 OpenViking memory：

```text
viking://user/<user>/memories/cases/<case_name>.md
```

对应模板是 `openviking/prompts/templates/memory/cases.yaml`，内容包括：

```md
# <case_name>

## Task Signature
...

## Input
...

## Rubric
...

## Evidence
...

## Linked Experiences
- [some_exp](../experiences/some_exp.md)
```

`## Linked Experiences` 是 tau2 VikingBot 后续找 exp 的关键入口。

### 2.2 Trajectory：一次执行的诊断轨迹

代码模型：

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

Trajectory 文件写在：

```text
viking://user/<user>/memories/trajectories/<trajectory_name>_<timestamp>.md
```

抽取逻辑在 `TrajectoryRolloutAnalyzer`：

1. 输入 `Rollout.messages` 和 `Rollout.evaluation`。
2. 把 evaluation feedback 追加进 extraction messages。
3. 通过 `AgentTrajectoryContextProvider + ExtractLoop` 只允许写 `trajectories` memory。
4. `MemoryUpdater` 将 trajectory 落盘。
5. `_read_trajectories(...)` 再把新写入 URI 读回 `Trajectory` 对象。

Trajectory schema 在 `openviking/prompts/templates/memory/trajectories.yaml`。它不是给未来 agent 直接照做的“经验”，而是一次 rollout 的成败审计：

- outcome / reward
- expected vs actual
- 决定性 tool facts
- first critical deviation
- 失败机制 / 关键反思
- 正确做法 / 泛化规则

### 2.3 Experience：可复用执行策略

当前 domain 里 `Experience = Policy`，`ExperienceSet = PolicySet`，加载逻辑在 `ExperienceSetLoader`：

```text
viking://user/<user>/memories/experiences/*.md
```

Experience schema 在 `openviking/prompts/templates/memory/experiences.yaml`。当前要求 experience 内容严格分三段：

```md
## Situation
- Applies only to ...
- Does not apply to ...
- Required policy gates ...
- Allowed terminal tool ...
- Forbidden substitute actions ...

## Approach
- executable pseudocode with exact tool calls

## Reflect
- Pitfall(count=N): guardrail
```

Experience 是 agent-facing 的。未来 agent 看到的是这类可执行 guidance，而不是原始 rollout 全量过程。

## 3. 训练时从 Case 到 Experience 的链路

### 3.1 离线/本地抽象链路

`OfflinePolicyOptimizationPipeline.train(...)` 的抽象顺序：

```text
CaseLoader.batches()
  -> RolloutExecutor.execute(cases, policy_set, execution_context)
  -> PolicyTrainer.train_rollouts(rollouts, policy_set, ctx)
       -> RolloutAnalyzer.analyze(rollout)
            writes/reads Trajectory
       -> GradientEstimator.estimate(analysis, experience_set)
            Trajectory -> PatchSemanticGradient for experiences
       -> PolicyOptimizer.plan(gradients, latest_experience_set)
            merge patches into PolicyUpdatePlan
       -> PolicyUpdater.apply(plan)
            writes experiences
```

关键点：

- `Case` 是执行入口；每个 `Rollout` 必须带 `rollout.case`。
- `Trajectory` 是 analyzer 阶段的产物，先持久化到 `memories/trajectories`。
- `ExperienceGradientEstimator` 对每条 trajectory 单独跑 `AgentExperienceContextProvider + ExtractLoop`，得到 experience 的 before/after patch gradient。
- `PatchMergePolicyOptimizer` 把多个 gradient 合并成最终写入计划。
- `MemoryFilePolicyUpdater` 将 plan 写回 `memories/experiences`。

### 3.2 session.commit 路径的实际 tau2 训练链路

tau2 batch runner 当前常用的是 `SessionCommitPolicyTrainer`，它不是本地直接 apply experience，而是把 rollout messages 写入一个 OpenViking session，然后调用 `commit_session`。

`SessionCommitPolicyTrainer._commit_one(...)` 会提交以下消息：

```text
[CaseSpec message]
+ [rollout messages]
+ [evaluation message]
```

服务端 `compressor_v3` 有 Training CaseSpec fast path：

1. `_write_training_case_memory(...)` 先把 Case 写成 `memories/cases/<case>.md`。
2. `train_from_extracted_cases(...)` 对这个 case 对应的 rollout 继续训练：
   - `TrajectoryRolloutAnalyzer.analyze(...)` 抽取 trajectory。
   - `ExperienceGradientEstimator.estimate(...)` 从 trajectory 估计 experience gradients。
   - streaming trainer 合并并写入 experiences。
3. `_link_case_to_training_outputs(...)` 建链接：
   - `case --related_to--> trajectory`
   - `experience --derived_from--> trajectory`
   - 如果 plan item 的 source trajectory 属于该 case，则 `case --related_to--> experience`
4. `_render_case_links_from_template(...)` 重新渲染 case 文件，让 `## Linked Experiences` 显示这些 experience 链接。

因此 case memory 不是单纯日志；它是后续 tau2 检索 experience 的索引页。

## 4. 链接关系

当前重要链接如下：

```text
Case memory
  --related_to--> Trajectory memory
  --related_to--> Experience memory

Experience memory
  --derived_from--> Trajectory memory
```

更具体地：

- `TrajectoryRolloutAnalyzer` 写 trajectory，并把 `case_name` 放进 trajectory fields。
- `ExperienceGradientEstimator._operations_to_gradients(...)` 给每个 experience gradient 加：

```python
StoredLink(
    from_uri=<experience_uri>,
    to_uri=<trajectory_uri>,
    link_type="derived_from",
)
```

- `PolicyOptimizer` / `PolicyUpdater` 负责在合并和写文件时保留这些 source trajectory links。
- `compressor_v3._case_training_links(...)` 根据本次 analysis 和 plan/apply result 生成 case 到 trajectory/experience 的 links。
- case 文件的 `## Linked Experiences` 只渲染指向 `/memories/experiences/` 的 links。

可以把它理解为：

```text
case 是“题目索引”
traj 是“证据/诊断”
exp 是“可复用策略”
link 是“为什么这个题目会召回这些策略”的 provenance
```

## 5. tau2 VikingBot 如何使用 Experience

实现文件：`benchmark/tau2/train/rollout_executor_vikingbot.py`。

### 5.1 工具配置

`_configure_tools(...)` 会：

1. 移除默认 OpenViking tools：所有 `openviking_*` tool 都 unregister。
2. 注册 tau2 环境工具，例如查询/修改/取消/沟通/done 等。
3. 额外注册两个只服务 experience recall 的工具：
   - `search_experience(query, limit=10)`
   - `read_experience(experience_uri)`

所以 tau2 VikingBot rollout 中，agent 不能随意调用通用 OpenViking memory tools；它只能通过这两个受控工具走 case-linked experience recall。

### 5.2 Prompt 强制要求先加载 experience_loader

`_build_system_prompt(...)` 明确要求：

- 采取 task action 前必须使用 `experience_loader` skill。
- loaded experiences 只是 prior training guidance。
- 当前 policy、当前 tool result、当前 user facts 优先于 prior experience。

`_prepare_experience_loader_skill(...)` 会把 `benchmark/tau2/train/experience_loader_template/SKILL.md` 写入 rollout sandbox：

```text
skills/experience_loader/SKILL.md
```

`_execute_required_experience_loader_read(...)` 会在 agent loop 前自动执行一次：

```text
read_file(path="skills/experience_loader/SKILL.md")
```

这样做的效果是：agent 在真实开始处理 tau2 用户请求前，已经读过“如何搜索、筛选、读取经验”的 instruction。

### 5.3 search_experience 的检索方式

`search_experience` 的行为不是直接搜索 experiences 目录，而是：

1. 调 `VikingClient.search(...)` 搜索当前用户的：

```text
viking://user/<user>/memories/cases
```

2. 对每个匹配 case，读取 case markdown。
3. 解析 case 的 `## Linked Experiences` section。
4. 对每个 linked exp，读取 exp 的 `## Situation` snippet。
5. 返回 JSON candidates：

```json
{
  "query": "...",
  "target_uri": "viking://user/<user>/memories/cases",
  "candidates": [
    {
      "case_name": "...",
      "case_uri": ".../memories/cases/xxx.md",
      "task_signature": "...",
      "input_summary": "...",
      "experiences": [
        {
          "name": "...",
          "uri": ".../memories/experiences/yyy.md",
          "situation": "## Situation 的短摘要"
        }
      ]
    }
  ]
}
```

因此检索路径是：

```text
current task query
  -> similar case memory
  -> case.Linked Experiences
  -> experience Situation snippets
  -> gated read_experience
```

这比直接搜 exp 更保守：先用 case 找到“相似题目”，再用 case-exp link 找到“这个题目训练出来/关联过的经验”。

### 5.4 read_experience 的消费方式

`read_experience(experience_uri)` 校验 URI 必须在 `/memories/experiences/` 下，然后读取 markdown 返回给 agent。

`experience_loader` skill 要求两次 gating：

1. **读前 gate**：只根据 search result 中的 `situation` snippet 判断是否 plausibly apply。
2. **读后复核**：读取完整 exp 后，根据当前工具查到的具体事实再次检查 `## Situation` 的适用边界和排除条件。

如果不匹配，agent 应丢弃该 experience，继续按 policy 和当前 tool facts 行动。

## 6. 端到端真实数据例子：`tau2_airline_train_24`

下面参考本地真实 memory 数据：

```text
/Users/bytedance/.openviking/data/viking/default/user/default/memories/
```

选用这一组真实文件：

```text
cases/tau2_airline_train_24.md
trajectories/已飞航班取消请求处理_20260626114859.md
experiences/取消预订前先核验航班日期与已飞航段.md
experiences/取消预订资格核验.md
```

### 6.1 Case：真实题目入口

`cases/tau2_airline_train_24.md` 的核心内容：

```md
# tau2_airline_train_24

## Task Signature
tau2:airline:train:41

## Input
{
  "domain": "airline",
  "split": "train",
  "task_id": "41",
  "task_no": 24,
  "user_query": "... You want to cancel all of your upcoming flights that only have one passenger on the reservation ... You are Amelia Davis ... user id is amelia_davis_8890 ..."
}

## Linked Experiences
- [取消预订前先核验航班日期与已飞航段](../experiences/取消预订前先核验航班日期与已飞航段.md)
- [取消预订资格核验](../experiences/取消预订资格核验.md)
```

这说明：

- 这是一个 tau2 airline 训练 case，稳定 task id 是 `tau2:airline:train:41`。
- 用户目标是“取消所有只有一名乘客的 upcoming flights”。
- 这个 case 已经通过 `## Linked Experiences` 关联了两条经验。

它的 `MEMORY_FIELDS.links` 里还有更完整的 provenance：

```text
case -> trajectory/不符合条件的取消转人工_20260626105904.md
case -> trajectory/多预订筛选与资格核验_20260626105904.md
case -> trajectory/批量取消单乘客预订核验_20260626112410.md
case -> trajectory/已飞航班取消请求处理_20260626114859.md
case -> experience/取消预订前先核验航班日期与已飞航段.md
case -> experience/取消预订资格核验.md
```

也就是说，这个 case 既连到了“证据轨迹”，也连到了“可复用经验”。

### 6.2 Trajectory：真实 rollout 诊断

`trajectories/已飞航班取消请求处理_20260626114859.md` 的核心内容：

```md
# Evaluation 信号
- Outcome: success.
- Reward: 1.0.
- DB/Communicate: DB 匹配成功, COMMUNICATE 匹配成功.
- Key expected signal: 读取用户详情和所有预订详情, 确认无待取消航班后告知用户.

# Expected vs Actual
- Expected: 读取用户详情, 读取所有预订详情, 确认无待取消航班后告知用户并结束任务.
- Actual: 按预期完成读取用户详情, 读取所有预订详情, 确认无待取消航班后告知用户并结束任务.
- Delta: no material delta.

# 事实链与偏离
- User/task intent: 用户 Amelia Davis 要求取消所有单人乘客的即将到来航班.
- Decisive tool facts: 所有预订航班日期均在 2024 年 5 月, 当前系统时间为 2024-05-15, 航班均已起飞.
- Correct target/path: 读取用户详情, 读取所有预订详情, 确认无待取消航班后告知用户.

# 关键反思
- 核心教训: 处理取消请求前需先读取用户所有预订并检查航班状态, 只有未飞航班才可能取消.
```

它的 `MEMORY_FIELDS` 明确标出：

```json
{
  "trajectory_name": "已飞航班取消请求处理",
  "outcome": "success",
  "retrieval_anchor": "意图: cancellation; 阶段: terminal_handoff; 终态动作: done; Policy gates: 航班是否已飞; 失败模式: none; 目标: 检查所有用户预订并判断无待取消航班.",
  "case_name": "tau2_airline_train_24"
}
```

这条 trajectory 不是未来 agent 直接照抄的操作手册，而是在记录：这次 case 为什么成功、关键 runtime facts 是什么、可泛化 lesson 是什么。

它的 backlinks 也验证了关系：

```text
experience/取消预订前先核验航班日期与已飞航段.md --derived_from--> trajectory/已飞航班取消请求处理_20260626114859.md
experience/取消预订资格核验.md --derived_from--> trajectory/已飞航班取消请求处理_20260626114859.md
case/tau2_airline_train_24.md --related_to--> trajectory/已飞航班取消请求处理_20260626114859.md
```

### 6.3 Experience：真实可复用策略

`experiences/取消预订前先核验航班日期与已飞航段.md` 的核心内容：

```md
## Situation
- 仅适用于用户要求取消预订的场景（包括取消单个预订、取消重复/多余预订、批量取消符合条件的预订），且用户已提供必要身份信息。
- 不适用于仅查询预订信息、修改航班、或已完成终态动作后的补充解释场景。
- 必须先满足的政策条件：已获取用户 id 并读取用户资料及所有相关预订详情；已知当前政策时间用于判断航段是否已飞。
- 允许的终态工具：`transfer_to_human_agents`（仅当存在已飞航段或不符合取消条件时），`cancel_reservation`（仅当无已飞航段且符合取消条件并经用户确认后）。
- 禁止替代动作：不得在未检查航班日期与当前时间的情况下直接 transfer；不得跳过取消 eligibility gate 直接取消或拒绝；不得在已确认全部预订均含已飞航段时仍尝试调用 cancel_reservation。

## Approach
- `user = get_user_details(user_id)`
- `reservations = [get_reservation_details(res_id) for res_id in user.reservation_numbers]`
- `current_time = 从系统提示或环境中获取的当前政策时间`
- 筛选出用户请求取消的目标预订（根据用户描述的航线、日期等特征）
- ...
- IF `all_have_flown == True` THEN
  - `transfer_to_human_agents(summary='用户请求取消多个预订但所有航班均已飞的详细情况')`
  - `communicate_with_user("YOU ARE BEING TRANSFERRED TO A HUMAN AGENT. PLEASE HOLD ON.")`
  - `done()`
  - STOP
- 对于无已飞航段且符合条件的预订，继续检查取消条件（24h 内预订、航司取消、business 舱、有保险且原因覆盖）
- 若符合取消条件，列出可取消的预订并获取用户确认后调用 `cancel_reservation`

## Reflect
- 易错点（踩坑次数=3）：处理取消请求时，未比较航班日期与当前时间就直接 transfer，导致错失处理符合条件取消的机会；... 若会话时间明显晚于所有航班日期（如 2026 年 vs 2024 年），应直接判断全部已飞并立即转接，绝对不得尝试调用 cancel_reservation。
```

可以看到它已经是 agent-facing 策略：

- `Situation` 定义什么时候可用、什么时候不可用。
- `Approach` 写成具体工具调用路径。
- `Reflect` 累积踩坑计数和 guardrail。

这条 experience 的 `MEMORY_FIELDS` 里也有真实版本和链接信息：

```json
{
  "experience_name": "取消预订前先核验航班日期与已飞航段",
  "status": "draft",
  "version": 8,
  "links": [
    ".../trajectories/已飞航班取消处理_20260626105854.md",
    ".../trajectories/处理已飞航班取消请求_20260626112309.md",
    ".../trajectories/已飞航班取消请求处理_20260626114859.md",
    ".../trajectories/重复预订已飞航段处理_20260626114853.md",
    ".../trajectories/已飞航班处理流程_20260626114853.md",
    ".../trajectories/已飞航班的取消与转接处理_20260626115000.md"
  ],
  "backlinks": [
    "case/tau2_airline_train_25.md",
    "case/tau2_airline_train_26.md",
    "case/tau2_airline_train_24.md"
  ]
}
```

所以一条 experience 可以由多条 trajectory 逐步强化，不只来自单次 rollout。这里 `version=8`、`踩坑次数=3` 就体现了“多次训练累积”的效果。

另一个被同一 case 链接的经验 `experiences/取消预订资格核验.md` 更宽一些，核心 gate 是取消资格本身：

```md
## Situation
- 仅适用于用户要求取消当前目标预订并获得退款，且需要先通过读取工具核验取消资格。
- 必须先满足的政策条件：收集用户id、预订id、取消原因；核验预订是否满足任一取消条件（24小时内预订、航司取消、商务舱、有旅行保险且取消原因是健康或天气原因）；无任何航段已飞行。
- 允许的终态工具：`transfer_to_human_agents`（当取消条件不满足时），`cancel_reservation`（仅当所有取消条件满足且用户确认后）。
```

它的 `version=16`，`Reflect` 中 `易错点（踩坑次数=10）`，说明它是更高频、更通用的取消资格经验；而 `取消预订前先核验航班日期与已飞航段` 是更聚焦“已飞航段 / 航班日期”这个边界。

### 6.4 真实数据里的关系图

基于上述文件，真实关系可以画成：

```text
cases/tau2_airline_train_24.md
  --related_to--> trajectories/已飞航班取消请求处理_20260626114859.md
  --related_to--> experiences/取消预订前先核验航班日期与已飞航段.md
  --related_to--> experiences/取消预订资格核验.md

experiences/取消预订前先核验航班日期与已飞航段.md
  --derived_from--> trajectories/已飞航班取消请求处理_20260626114859.md
  --derived_from--> 其他已飞航段/取消处理 trajectories...

experiences/取消预订资格核验.md
  --derived_from--> trajectories/已飞航班取消请求处理_20260626114859.md
  --derived_from--> 多条取消资格核验 trajectories...
```

这正好对应前面的抽象链路：

```text
Case(tau2_airline_train_24)
  -> Rollout(success, reward=1.0)
  -> Trajectory(已飞航班取消请求处理)
  -> Experience(取消预订前先核验航班日期与已飞航段 / 取消预订资格核验)
```

区别是：真实数据里 experience 是跨多个 rollout 累积合并后的结果，不是一条 trajectory 一次性生成的孤立文件。

### 6.5 tau2 VikingBot 下次如何用这组真实数据

假设新 case 仍然是 airline cancellation，用户说：“取消我所有单人预订的 upcoming flights”。VikingBot rollout 中会先读 `experience_loader`，然后可能执行：

```json
search_experience({
  "query": "airline cancel upcoming flights single passenger reservation cancellation eligibility flown segments",
  "limit": 10
})
```

`search_experience` 搜的是 `memories/cases`，所以它可能命中：

```text
cases/tau2_airline_train_24.md
```

然后读取这个 case 的 `## Linked Experiences`，返回两个候选经验：

```json
{
  "case_name": "tau2_airline_train_24",
  "task_signature": "tau2:airline:train:41",
  "experiences": [
    {
      "name": "取消预订前先核验航班日期与已飞航段",
      "uri": "viking://user/default/memories/experiences/取消预订前先核验航班日期与已飞航段.md",
      "situation": "仅适用于用户要求取消预订... 必须先...读取用户资料及所有相关预订详情... 判断航段是否已飞..."
    },
    {
      "name": "取消预订资格核验",
      "uri": "viking://user/default/memories/experiences/取消预订资格核验.md",
      "situation": "仅适用于用户要求取消当前目标预订并获得退款... 核验预订是否满足任一取消条件... 无任何航段已飞行..."
    }
  ]
}
```

agent 按 `experience_loader` 的规则先做读前 gate：

- 当前是取消预订任务，匹配 `取消预订前先核验航班日期与已飞航段`。
- 当前需要判断 upcoming / 已飞，匹配它的 `Situation`。
- 当前也涉及取消资格，所以 `取消预订资格核验` 也可能适用。

于是 agent 再调用：

```json
read_experience({
  "experience_uri": "viking://user/default/memories/experiences/取消预订前先核验航班日期与已飞航段.md"
})
```

读完后，agent 不应立刻照搬结论，而是执行 experience 要求的 facts collection：

```text
get_user_details(user_id)
get_reservation_details(res_id_1)
get_reservation_details(res_id_2)
...
```

然后根据当前 case 的真实工具结果分支：

- 如果所有目标航班都已飞：按经验走 `transfer_to_human_agents` / `communicate_with_user` / `done` 或直接告知无可取消航班，取决于当前 policy/evaluation 边界。
- 如果存在未飞且符合取消条件的单人预订：不能因为旧 trajectory “已飞”就拒绝，而应继续检查 24h、航司取消、business 舱、保险原因等取消 gate，必要时读取/应用 `取消预订资格核验`。
- 如果不符合取消资格：不得调用 `cancel_reservation`，应转人工或说明。
- 如果符合且用户确认：才能调用 `cancel_reservation`。

这就是“真实数据中的 exp 使用”：**case memory 召回候选 exp，exp 的 Situation 控制适用性，Approach 指导工具路径，当前工具事实决定最终分支。**

## 7. 为什么不直接搜 experiences？

当前 tau2 VikingBot 选择 `case -> linked exp`，主要收益：

- **降低过宽 experience 噪声**：先找相似题目，再拿这个题训练关联过的 exp。
- **保留 provenance**：case 文件说明该 exp 是从哪些训练题/轨迹来的。
- **支持 gating**：search result 只返回 `## Situation` snippet，鼓励先判断适用性，再读取全文。
- **避免注入过多经验**：不是把 experiences 全部塞 prompt，而是按需 search/read。

代价：

- 如果 case memory 没有被写入或没有 `## Linked Experiences`，tau2 recall 就拿不到对应 exp。
- 如果 case search 没命中，即使 experiences 目录里有相关经验，也可能不会被发现。
- 如果 experience 的 `## Situation` 写得过宽，仍可能误用；因此当前 schema 对 Situation 的适用/排除/terminal tool/forbidden substitute 要求很严格。

## 8. 文件定位速查

| 主题 | 文件 |
|---|---|
| domain dataclass | `openviking/session/train/domain.py` |
| tau2 CaseLoader | `benchmark/tau2/train/case_loader.py` |
| tau2 VikingBot executor | `benchmark/tau2/train/rollout_executor_vikingbot.py` |
| experience_loader skill | `benchmark/tau2/train/experience_loader_template/SKILL.md` |
| trajectory analyzer | `openviking/session/train/components/trajectory_analyzer.py` |
| experience gradient estimator | `openviking/session/train/components/gradient_estimator.py` |
| patch merge optimizer | `openviking/session/train/components/policy_optimizer.py` |
| memory file updater | `openviking/session/train/components/policy_updater.py` |
| session.commit trainer | `openviking/session/train/components/session_commit.py` |
| CaseSpec fast path / case links | `openviking/session/compressor_v3.py` |
| case schema | `openviking/prompts/templates/memory/cases.yaml` |
| trajectory schema | `openviking/prompts/templates/memory/trajectories.yaml` |
| experience schema | `openviking/prompts/templates/memory/experiences.yaml` |
