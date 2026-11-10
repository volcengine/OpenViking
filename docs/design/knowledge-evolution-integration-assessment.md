# F2F → OpenViking 可控知识进化：集成评估

日期：2026-11-06。状态：按用户六项批注修订的设计评估，未实现服务、未迁移线上数据。

## 1. 结论与代码基线

可行。建议把 F2F 的证据、提案、反馈、Issue、审查和发布契约集成到 OV，形成按知识空间配置的进化服务。Fermat 翻译保留为第一个业务适配器。新链路不依赖 DAG、GRPO、Case→Experience 固定归属，也不要求把所有学习结果存成 experience。

首版按单实例、目标知识空间独占维护来做，不以全平台并发调度或原子可见改造作为前置条件。范围包括 experience memory 和其他文本知识，默认只允许修改 experience 路径；固定负票阈值自动回滚和 human ground truth 是保留的产品行为。主要工程是泛化契约、改造紧凑轨迹、接入发布与故障恢复。

- 基线：新获取的 `origin/main`，`a5f08d14eb9305803402e6ab71b88a66bf897187`。
- 工作分支：`feat/exp_cl`，已基于上述 main 建立。
- 旧分支内容保存在 `backup/exp_cl-before-main-20261106`，提交 `4ddf80a3bbb892a701ce41eaf017c1017c33aa27`。
- 独立工作树：`/Users/bytedance/Downloads/OpenViking/.local-data/exp-cl`。原工作目录和未跟踪实验文件保留。
- F2F 源：`/Users/bytedance/Downloads/f2f_translate/f2f_project`，本次只读检查，未修改。

旧 exp_cl 包含大量 DAG 及独立轨迹实验变化，不适合整支合并或整体 cherry-pick。备份中的 `openviking/trajectory_evolution/` 可提供轨迹引用、证据预算、恢复测试的参考，但不能把本地实验工件后端、tau2 语义或元学习流程作为新服务的基础。

“不要 DAG”在本方案中的含义是：不引入实验分支的 DAG 代码，新服务不调用原有 Experience 学习入口。main 已存在的普通记忆和旧自进化兼容接口不在此评估中直接删除。

## 2. 代码核对带来的重要修正

### 2.1 main 的 Trajectory 与此前实验分支不同

最新 main 的 `openviking/system_assets/memory/memory_templates/builtin/trajectories.yaml` 定义的是提炼后的 reusable operation contract，包含 procedure、适用边界和泛化后的 evidence；embedding 使用 `trajectory_name + retrieval_anchor`。

因此，前面对 DAG 分支所作的“Trajectory 是事实步骤、embedding 使用正文”的说明，不能直接套用到这个 main 基线。

决定直接改变新链路的 Trajectory 语义：按顺序增量累计紧凑的原始执行记录，不再生成未来执行用的操作指导。记录用户请求、assistant 行为、工具名称和关键输入输出、时间/事件 ID、结果以及反馈来源。重复内容可以引用，大工具输出保留关键片段和原始产物地址；不能省略会改变判断的参数、失败信息或条件。

Session archive 保留完整原文作为回查来源。每个紧凑片段绑定原始事件范围和内容摘要；反思、归因和建议另存，不混成已发生的事实。每次 commit 追加新片段，不反复重写整条历史，读取时按事件顺序组合。

新格式显式记录 format/version。旧操作指导型 Trajectory 不自动冒充事实轨迹，保留旧格式标识或离线重建。检索文本和 experience schema 后续讨论，不保留现有 procedure 模板作为约束。

### 2.2 F2F 已有的是提案审查，不是通用轨迹学习

`f2f_primary_reflect.py` 登记运行产物和事件证据，提交 proposals、issues、issue_updates、feedback 或 no_change_reason；`f2f_evolution/worker.py` 冻结提案和知识基线；`f2f_evolution_vikingbot.py` 让 AgentLoop 在只读投影内审查。实际发布由 worker 和 `f2f_evolution/ov.py` 执行。

原始事实、agent 自我解释、评价结果、建议修改需要继续分别表示。接收端不能把模型声明的“验证成功”当作服务端已验证结果。

### 2.3 OV 可复用较多能力，但不等于已经具有进化事务

- `openviking/server/identity.py`、URI 校验和 ACL：提供原生身份与访问控制。
- `openviking/session/session.py`、`archive_store.py`：提供归档、摘要、原始消息和恢复入口。
- `openviking/service/task_store.py`、`external_task_service.py`：提供任务身份、状态、持久化执行和外部 provider 模式。
- `openviking/service/compile_service.py`、`compile_targets.py`：已有目标目录、任务编排、文件版本引用和快照集成，应复用通用能力，避免再造相同底座。
- `openviking/service/memory_compile.py`：已有 memory consolidation，但它不是证据驱动的提案审查器，不能直接替代新服务。
- `openviking/server/routers/content.py` 的 batch-write 支持 `expected_sha256` 和 `if_absent`，可以做条件写入。
- `openviking/storage/content_write.py` 允许部分写入失败，相关行为在 `tests/server/test_content_batch_write.py::test_batch_partial_failure_refreshes_successful_files_and_retry_is_safe` 中有覆盖。不能把 batch-write 当作整批原子事务。
- Snapshot 保存文件版本，不保存历史向量索引或 ACL，也不是任意并发 I/O 的全局原子视图。参见 `docs/zh/guides/15-snapshot.md`。

## 3. 对外契约

### 3.1 EvolutionSpace 是一等配置对象

建议引入空间对象，绑定以下字段；以下名称是设计建议，不是已存在的 API。

- `space_id`、账户、owner、授权的提交者/审查者/发布者。
- `read_roots`、`write_roots`、明确的 `deny_paths`。
- `write_scope_mode = allowlist | owned_roots`：限制开关关闭后也只能修改该空间登记且有权限的根，不能退化成整个账户或整个 OV。
- `reflection_mode = agent | server | hybrid`，以及固定版本的反思、审查策略。
- `publication_mode = automatic | manual`；自动发布仍须通过权限、证据和版本检查。
- 固定负票阈值、`issue_policy`、预算、触发和保留规则；负票达阈值自动回滚，human 输入按 ground truth 处理。
- `policy_revision`、当前已发布 revision、发布与索引状态。

默认配置仅开放 `viking://user/<user_space>/memories/experiences/`（用户所称 memory/experience）。其他文本知识路径按名单显式添加。显式设置空 allowlist 表示不可写；空间配置与知识内容分别授权。首次只支持规范化 URI 前缀，不提供任意 glob。第一版拒绝两个进化空间拥有重叠的可写子树，减少发布锁和版本归属歧义。

空间路径不能绕过原生 ACL。后台发布用受限的空间身份，不能把 F2F 的本地管理凭证模型平移成所有 worker 的全局写权限。排队期间权限或空间策略变化，发布前必须重新检查。

### 3.2 两种反思入口，共用输出与发布

```text
外部 Agent Reflect ── 证据引用＋提案/Issue/反馈 ──┐
                                                ├─ 统一接纳、审查、候选、发布
紧凑原始 Trajectory ── OV/Vikingbot 反思 ────────┘
```

Agent 侧沿用泛化的 reflect skill；服务端消费已持久化的紧凑原始轨迹，必要时回查 archive，运行反思 provider。hybrid 以 agent 的反思作为假设，结合事实核对，避免无条件重复两次完整分析。

统一对象建议：

- **Execution/Evidence**：run、session/archive、任务要求、结果、工具执行、环境事实、读知识记录；原始内容不可变且可校验。
- **Reflection**：分析者和其策略/模型版本、证据引用、判断、未知点。不是事实本身。
- **Proposal**：空间、来源、基线、读集/写集、适用范围、修改理由、证据引用、预期效果和具体 operations。
- **Issue**：问题描述、影响、尝试、未知点、所涉知识版本；可以没有解法，也可以没有发生错误。
- **UsageFeedback**：目标 URI/块/方案与版本、run、实际使用观察、scope、vote；未使用不能投实际效果票。
- **Candidate/Publication**：冻结的最终字节、审查结果、策略版本、前后版本、写入及索引回执、恢复状态。

原始执行 evidence 与审查记录保留在账户隔离的私有存储，公开知识只保留适合公开的结论、范围和可授权访问的来源引用。不得将完整轨迹默认混入公开全文检索。

### 3.3 Session 集成与外部执行兼容

OV 侧把已完成归档转换为新的紧凑原始 Trajectory，记录 `archive_uri + messages_sha256`；外部 agent 可上传同一事实记录契约及独立产物引用，不必先生成 main 的操作指导型 trajectory memory。

归档成功后记录持久化的进化触发事件，异步排队分析，不把昂贵模型分析塞进 commit 请求。明确区分归档成功、反思完成、提案接纳、发布完成；Session 提取失败也不应导致已保存的原始证据永远不可分析。

归档写入与触发记录存在崩溃窗口：需要 outbox 或基于归档的可重建扫描。消费身份包含账户、空间、archive 摘要、反思策略版本，重复投递不能重复发布。单个 run 可能跨多次 commit，不能把中途未完成片段当作最终失败；结果和明确反馈都是可选项，没有就记录 unknown，仍允许保存轨迹、分析事实缺口和提交 Issue。

Vikingbot 的审查任务不得又自动进入相同空间的反思链路。记录 execution purpose，排除 reviewer/system-maintenance 自触发；同一执行同时来自外部 reflect 和服务端分析时保留来源、关联去重。

## 4. F2F 组件迁移边界

### 优先迁入语义和测试，适配实现

- `store.py`：不可变证据、幂等对象、冻结批次、revision；改成 OV 身份和存储接口，去掉 single-space 常量。
- `worker.py`：提案审查、候选摘要、发布和恢复状态机；去掉业务门禁，首版保留单实例串行执行，适配原生任务和存储。
- `feedback_retry.py`：冻结输入、持久化重试预算、拒绝在过期输入上修复；与 OV 任务重试统一，避免嵌套重试放大成本。
- `issues.py`、`workarounds.py`：问题及方案版本、人工回应、历史证据；把错误 signature、build gate 等改成可选业务扩展。
- `blocks.py`：版本化反馈和修改归因；需要通用目标模型，不依赖 translation_agent/case_id。
- `observations.py`：URI/版本/片段的实际读取记录；接入原生读取工具，保留“提供给调用方”与“模型采纳”之间的区别。
- `merges.py`、`scopes.py`：保留分支范围和证据、不无依据扩大结论；错误签名归并及 Fermat 判断规则移入业务 profile。
- `f2f_evolution_vikingbot.py`：复用受限审查上下文、增量读取和结构化输出设计；重做 provider，不保留 shell 子进程/私有配置文件作为核心接口。

### 不迁入 OV 核心

- Fermat/Fermi 翻译规则、CloudIDE、Build/Metadata 工具链。
- F2F Docker/CLI 启动器、客户文件准备、model ID 约束。
- 写死 `resources/f2f`、`resources/error-recover` 的布局与 reviewer prompt。
- 独立 HTTPServer、运行令牌系统、重复的全套 release registry。
- 项目自带的旧 OV engine 快照。
- 旧 exp_cl 的 DAG、GRPO、tau2 controller、本地模拟 OV 后端。

业务 profile 和外部接入示例可放 `examples/knowledge-evolution/f2f/`；F2F 客户执行包保持外部管理，框架仅记录其 release_id。未来执行技能自修改需要独立范围和验证规则，不作为首版默认能力。

## 5. 发布和版本是核心，不是附属日志

### 5.1 四种版本不能混用

1. 执行者版本：模型/策略/工具或执行包的标识，用于解释行为来源。
2. 实际读取版本：每次知识 read 的 URI、内容摘要、可选 snapshot/片段引用。
3. 提案基线：分析时参考的读集和拟修改的写集版本。
4. 发布版本：经审查的候选、snapshot 和生效状态。

F2F 的 V2 是 live-observed 知识读取，并不因起始 base_revision 就保证整轮固定知识快照。当前固定的执行包 release 也不是知识发布 revision。

### 5.2 推荐首版保证

采用单实例、目标空间独占维护、一次一个更新任务。首版默认进化期间没有 Compile、同步任务或人工编辑修改同一空间，由操作者安排维护时段；不同空间不需要整体停机。保留开始/结束状态、旧内容、条件写入和失败恢复，不要求先统一全部服务的调度。

并发问题与中途失败问题不同：即使没有第二个任务，进程仍可能在更新两个文件之间停止。最小恢复机制仍需保留，但不需要为此先实现数据库级事务。

例如一次修改 A.md 与 B.md：A 已更新、B 尚未更新时断电，下次启动应能知道本次没有完成，恢复旧内容或继续同一份已审查修改，而不是把一半结果记为成功。

建议状态：

```text
COLLECTING → REVIEWING → READY → APPLYING → INDEXING → PUBLISHED
                  └→ REJECTED / CONFLICT
                               APPLYING / INDEXING → RECOVERY_REQUIRED
```

发布过程：

1. 冻结 candidate 字节、read/write preconditions、策略版本、review digest 和幂等 publication_id。
2. 在任何原地写入前保存本次计划、旧内容和进行中状态，进入该空间的独占维护阶段。
3. 重新检查身份、路径、当前权限、策略和基线；创建用 if_absent，更新用 expected_sha256。
4. 记录每个文件的执行回执；恢复时按当前字节区分未写、已写、出现外部变更，不能盲目重放 append。
5. 正文回读、建立范围正确的 snapshot、刷新派生摘要和向量索引，检查失败记录。
6. 记录正式发布版本，结束维护状态；索引失败则保留未完成状态，支持重试，不能宣称整个更新完成。

当前 batch-write 的 operations 不包含 delete/move。若首版支持归并后删除旧文档，需要额外实现受同一发布边界保护的条件删除；不能先校验 hash、释放锁后再普通 rm。首版也可明确只开放 create/update，把删除与移动留到该能力就绪后。

需要处理“文件写完、snapshot 成功、索引成功、发布指针更新”每两个阶段之间的崩溃窗口。任务返回成功、正文已可见、搜索已更新、知识变更有效，是四件不同的事。

### 5.3 首版独占运行，后续按空间 FIFO 调度

并发冲突的具体例子：进化与 Compile 同时读到旧文件，各自计算新内容；进化先写入，Compile 随后仍按旧内容写回，覆盖刚学到的知识。首版既然默认同一空间没有其他写者，就不需要先解决多写者协调。expected_sha256 只作为发现意外变化的简单保险，不扩展成全平台调度工程。

以后可以把同一空间的进化、Compile、资源同步和人工更新放进一个 FIFO 队列，一个任务完成后再执行下一个。它更接近串行作业队列，不要求操作系统式的抢占和完整 context save/restore。排队任务开始时读取最新基线；若早先已产生候选，执行时检查旧版本，避免排了队却仍写入过期结果。

FIFO 解决写任务互相覆盖，不解决任务自身执行到一半失败；后者由上一节的发布记录与恢复负责。先按空间串行，不做全 OV 全局队列；跨空间更新将来再明确调度规则。

首版不承诺进化维护过程中所有在线读取都只看到完整旧版或新版。若业务需要此保证，可以在短暂发布阶段暂停该空间的读取，或后续增加版本化读取；这不是首版的前置工程。

最终写入仍必须检查 allowlist，删除/移动检查源和目的；服务端字段和生成摘要不能让模型任意覆盖。原生普通记忆提取也算同路径写者，首版不能同时启用旧 Experience 生成链路来修改新进化拥有的 experience 文件。

索引可以理解为检索目录：正文写好了，检索目录可能还没更新。首版等待它更新完成再标记发布成功，失败就重试；不需要为每个历史版本保留一整套历史向量索引，回滚后重新生成当前索引即可。

### 5.4 回滚也是新发布

按空间/目标版本恢复，不能把整个账户 snapshot 的所有内容恢复以撤销一个项目变更。历史证据和负反馈不可删除。旧版知识若已被后续正确修改，必须做当前版本检查和冲突处理，不能无条件写回整文件。

Snapshot 不保存 ACL，恢复旧内容时仍按当前权限执行。新空间启用前必须验证 version backend 可用；强版本模式不能在 git 被关闭时静默降级为“有日志就算有版本”。

## 6. 反馈与人类入口：保留 F2F 的核心行为

- 必须保留固定负票阈值自动回滚，不改成只触发分析或 Issue。首版沿用 F2F 的现有数值 `score = upvotes - downvotes <= -2` 且 `downvotes >= 2`，阈值固定并由程序执行，不让模型临时决定；具体数值可另行明确调整。
- F2F 当前按 case/version 折叠有效票。通用系统要定义 run、actor、tenant 的身份和去重规则；随机换 run_id 不能无限放大权重。
- 投票针对实际读取/使用的版本，不能只凭 run 初始 baseline；V2 live-observed 模式尤其需要使用 read receipt。
- 同一知识对不同配置有效性不同。保留适用范围、实际结果和证据；未命中条件与执行失败不应自动成为负票。
- Markdown block 标记不是唯一通用定位方式。第一版支持文件版本和可选片段锚点；保留 F2F 块版本适配。移动、拆分、合并后的反馈保留原始目标，不能悄悄搬到新文本上。
- Issue 不只表示工具报错：还包括知识缺失、冲突、过期、环境变化、缺少前提和无法判定。重复问题可以合并展示，但各自证据与适用范围保留。
- Human 输入视作 ground truth，不增加普通留言/专家裁决的二次语义审查层。沿用经过认证的人类输入通道；agent 转述或日志里出现的文字不自动变成人类提交。Vikingbot 负责组织、定位和发布，不要求人类重新证明说明。明确的人类纠正优先于模型推断；适用范围照实保留。
- 保留 F2F 对 human ground-truth 内容的保护，不能由 agent 负票回滚撤销人类修正。负票阈值回滚面向自动进化产物；人类版本仍进入版本历史，后续 human 修订明确留痕。
- Issue 的受理、知识发布、方案验证、业务任务恢复分别记录；“triaged/已回应”不等于问题已修复。
- 环境认知需 scope、observed_at、环境/工具版本及可选有效期。临时网络失败不能泛化成工具永久不可用。

### 没有显式 feedback 的运行与后续 next-turn 标注

真实运行不要求每次都有 evaluator 或用户评分。允许 outcome/feedback 缺省；未知不视为失败，不阻止积累轨迹、接收反思或发现知识漏洞。已有工具结果、用户要求和后续事实可以支持局部修正，不能编造整轮成功。

后续考虑类似 OpenClaw-RL 的 next-state/next-turn signal：用户下一次回复、纠正、重试请求或工具结果，可提供对前一步的评价以及改进方向。[OpenClaw-RL 论文](https://arxiv.org/abs/2603.10165) 使用这些信号训练策略；这里借鉴的是信号采集与标注，不引入其 RL/GRPO 训练链路。

首版只预留关联字段与延迟反馈入口：上一动作/回答 ID、后续观察 ID、原始来源、标签、标注器版本、适用范围。下一轮或下一次 commit 到达后追加标注，不重写旧执行事实。没有后续反馈、用户沉默或换话题，保持 unknown。

步骤被判负不等于某条 experience 应被投负票。只有能关联到实际使用的知识版本时才形成对应知识反馈；无法归因时先保留执行级信号。next-turn 自动标签是否计入固定回滚阈值后续明确，首版不擅自把推断标签当成显式投票。人类原始纠正是 ground truth，模型对它的解释仍需保留原文来源。

## 7. 其他尚需明确的问题

### 多类型写入

“资源”和“memory”在 OV 有不同语义。修改 memory 必须经过 schema、MemoryUpdater、links/backlinks 和摘要/索引维护，不能用 resources 的原始文本 writer 直接改 MEMORY_FIELDS。

首版范围明确包括 experience memory 与其他文本知识，默认只允许修改 experience 目录；文本 resource 路径按名单开启。experience 的具体 schema 后续讨论，不绑定现有 DAG、procedure 或固定 Case 结构。仅做最小存储适配：服务端管理身份/版本/来源等元数据，模型修改允许的内容，更新摘要与索引。不是首版必须实现所有 memory 类型的通用适配。可执行 skill、二进制文件、ACL 修改默认不纳入首版。

### 知识整理与原始资料

知识文档可能由源仓库同步或 Compile 生成。首版独占期间停用同路径更新；维护后若重新同步仍可能覆盖进化结果，因此路径名单优先选择 experience 或独立知识目录。后续通过 FIFO 与内容归属规则协调；FIFO 本身不能决定人工改动是否应该被下一次全量同步保留。

### 语义与机械正确性

路径合法、patch 匹配、证据引用存在，并不证明因果分析正确。review 要允许 no_change、conflict 和 Issue；成功执行不是每个步骤都必要的证明。结构错误的修复重试不能改写证据；语义重审需要新的 review 版本。

候选发布与效果提升分开计量。保留未参与提炼的评估任务，观察新增规则误用、旧能力退化、检索成本及用户修改率；不能用接纳率作为进化有效性的唯一指标。

### 生命周期、成本与保留

按空间配置数量/时间/手工触发，合并重复事件并设置预算；保留负例与 unknown。模型超时、无候选、发布失败分别显示；发布重试不能重新调用模型生成不同候选。

轨迹和工具输出可能含敏感信息，存储权限、脱敏、保留期限及删除请求要与证据引用一致。GC 不能删除尚被候选、Issue、历史发布引用的证据；需要引用可达性与删除标记。

Vikingbot provider 可选：没有运行器时仍可接收证据和 agent-side proposal，但需要自动分析/审查的操作应返回明确的未配置状态，不能暗中跳过审查发布。分析工作区默认无发布凭据，最终写入由确定性发布器执行。

### 接口兼容

使用独立 `/api/v1/evolution/spaces/...` 命名空间较清晰；现有 `/agent-evolution` 留作兼容，不悄悄改变开关含义。Session opt-in 关联一个或多个已授权的 space，并记录 origin；普通 Session commit 不自动激活新链路。

建议接口族：空间配置、执行/证据登记、反思提交/触发、proposals、feedback、issues、reviews、publications、history、recovery。模型生成内容字段与服务端身份/时间/版本字段分离。

## 8. 推荐代码结构与接入点

以下为新增模块建议，不是当前已有接口：

```text
openviking/evolution/
  models.py / policy.py             契约、空间权限与策略
  store.py                          私有对象、事件与发布状态接口
  ingestion.py / reflection.py      外部提案及归档分析
  review.py                         语义审查与确定性校验边界
  publisher.py / recovery.py        条件写、版本、索引、恢复
  feedback.py / issues.py           投票、问题、人工回应
  backends/experience.py           首版 experience 内容与元数据适配
  backends/resources.py            首版名单内文本资源变更
openviking/service/evolution_service.py
openviking/server/routers/evolution.py
bot/vikingbot/evolution/             反思/审查 provider
examples/knowledge-evolution/f2f/    业务 profile 与接入示例
```

TaskTracker/ExternalTaskService 负责运行任务，不承载全部不可变业务对象；EvolutionStore 保存证据、提案和发布关系。参考 Compile 的账户私有状态及 PathLock 模式，版本正文尽量引用原生 snapshot，避免每次复制整个知识库到 SQLite JSON。

第一版采用单实例独占维护，接口以 account/space 为键；以后按空间接入 FIFO，不把跨实例调度作为本轮要求。不要额外启动 F2F receiver HTTPServer，也不要依赖业务项目在 sys.path 中存在。

## 9. 实施顺序与验收

### PR 1：契约、空间、持久化与路径边界

实现账户隔离、空间策略、Evidence/Proposal/Issue/Feedback 对象、幂等提交和查询；暂不发布。迁入 F2F 的证据不可变、重复请求、范围保留测试，改为原生身份。

验收：路径别名/编码/相邻前缀不越界，重叠空间拒绝，跨账户引用拒绝，重复 ID 不改变证据，缩小 allowlist 后旧候选无法继续发布。

### PR 2：原生发布与恢复

建立旧内容备份、条件写入、回读、snapshot、索引和恢复记录；在目标空间独占维护假设下实现，不先统一 Compile 等服务。支持 experience 和名单内文本资源，先用人工构造的候选，不依赖模型。

验收：断电/取消、部分写入、索引失败、snapshot 失败、回执丢失、重复发布、意外版本变化和冲突后禁止覆盖。只验收已开放的修改操作；维护期间不承诺全局读隔离，不以统一调度器为验收前提。

### PR 3：agent-side Reflect 与 Vikingbot review

泛化 Reflect 输出，接入审查 provider，共用 PR 2 发布器。迁入 F2F scoped evidence、merge、不可扩张范围、重试预算测试；用 F2F profile 做端到端回归。

验收：可以发现知识缺口并创建 Issue；同 scope 冲突不会并列发布；无变化不修改版本；模型无发布凭据；既有 F2F 示例能读取实际更新后的 OV 知识。

### PR 4：OV-side archive 分析

改造紧凑原始 Trajectory 的增量积累，接入归档持久化触发、反思 provider、hybrid 去重和 system-run 排除，不依赖固定 Case→Experience 或 DAG。

验收：轨迹保留顺序与可回查证据、旧格式不会被误认成事实；commit 返回与反思异步状态分离，归档后崩溃可补投递，无反馈可继续积累，多次 commit 不虚构终局，反思不递归触发，重复投递不重复发布。

### PR 5：反馈、人类 Issue 工作流与最小 UI/SDK

提供版本化投票、人工回应、问题状态、发布 diff、历史和恢复入口；接入 read receipts。API、CLI/SDK 与 Studio 使用同一身份和同一状态模型。

验收：旧版本票不污染新版本，重试不刷票，达到固定阈值自动回滚相应自动生成内容；human 输入按 ground truth 更新且不被 agent 负票撤销；原始证据受 ACL 控制；延迟反馈可关联已有执行，不要求每轮都有标注。

后续再做 next-turn 自动标注、同空间 FIFO、其他 memory 类型、可执行技能发布、跨实例部署、固定版本检索和自动策略/skill 自修改。experience schema 在实现其适配前单独讨论，不提前确定。PR 1–5 是同一最小完整产品的分阶段交付。

## 10. 首轮建议采用的决策

1. 新链路独立 opt-in，基于 main，不带 DAG；F2F 作为外部 adapter/profile。
2. 双反思入口统一 proposal；Trajectory 改为紧凑、增量累计的原始执行记录，archive 保留回查原文。
3. 首版支持 experience memory 和其他文本知识；experience schema 后续讨论。
4. 默认只允许修改 experience 路径，其他文本知识按名单开启；范围检查覆盖最终发布和恢复。
5. 自动发布可用，但必须绑定固定策略与版本检查；人工门禁按空间配置。
6. 必须保留固定负票阈值自动回滚，以及 human 输入视作 ground truth；保留范围和版本归因。
7. 首版单实例、同空间独占维护，默认无 Compile 等并发修改；后续按空间 FIFO，不先做全局 context-switch 系统。
8. 版本控制先实现清楚的旧内容、新内容、发布状态和失败恢复；复用 snapshot 与条件写，索引更新完成再标记发布成功。
9. 反馈是可选输入，没有就保持 unknown；为后续 next-turn 标注预留事件关联，不在首版引入 RL 或自动推断投票。

本次验证为源代码与已有测试定义的静态核对，未执行业务实验、未调用模型或云端 OV。文档不构成对现有云端部署版本与行为的验证。
