# Useful Memories Become Faulty When Continuously Updated by LLMs

## Episodic traces

对“发生过什么”的原始轨迹记录，是未经高层抽象的证据形态。

## Consolidated abstractions

跨多个 episodes 提炼出的、可复用的 schema-like lessons。许多 agentic memory 系统会持续维护这种文本 memory bank。

## Faulty memory

由原本有用的经验导出，但在 consolidation 过程中逐步变成不可靠、误导性甚至错误适用的记忆。

## Update schedule

指 memory update / consolidation 的时机、频率、批次与组织方式。本文强调：schedule 不同，memory 结果会不同。

## Static-Group

先按 task family 分组，再在组内做离线 consolidation 的策略。项目页将其描述为三种比较方案中最佳。

## Static-All

将所有经验放到单一池中统一抽象的离线策略。

## Stream Update

随交互流增量重写 memory bank 的策略，对应论文批评的典型持续 consolidation 设定。

## Retain / Delete / Consolidate

在 ARC-AGI Stream 受控环境中暴露给 agent 的三类记忆动作，用于显式研究 memory management 行为。
