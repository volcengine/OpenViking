# Useful Memories Become Faulty When Continuously Updated by LLMs

## Problem statement

很多 agent memory 方法默认认为：把成功轨迹总结为文本经验，再持续写回 memory bank，就能让 agent 随着交互不断变强。本文要检验的正是这一前提是否成立。作者的核心结论是否定的：持续 consolidation 并不天然带来稳定增益，反而可能让记忆本身逐步变坏。

## Observations

1. 当前不少系统偏向维护 **consolidated textual memories**，而不是长期保留原始 episodic traces。
2. 随着 consolidation 持续推进，memory utility 会呈现“先升后降”的走势，并可能低于 no-memory baseline。
3. 问题不只是“信息丢失”，而是形成了具有误导性的 faulty memory。
4. 同一批 trajectories 在不同 update schedule 下会导出定性不同的 memories，说明问题与更新机制本身强相关。

## Gap

既有工作更强调“经验总结”带来的压缩和泛化收益，而较少系统回答：

- consolidation 是否会引入结构性失真；
- 错误是否随更新轮次累积；
- 原始轨迹与抽象记忆之间应该如何分工；
- 记忆系统应如何 gate consolidation，而不是默认每次交互后都更新。

## Key insight

faulty memory 不是简单遗忘，而是 **带方向的错误抽象**。当 LLM 反复把过去经验改写成高层 lesson 时，抽象边界会逐步漂移；这些偏差被后续检索与决策继续使用，于是由“可复用经验”演变成“误导性规则”。
