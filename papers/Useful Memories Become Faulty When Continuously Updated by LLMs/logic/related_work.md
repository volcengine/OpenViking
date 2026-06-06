# Useful Memories Become Faulty When Continuously Updated by LLMs

本文把 agent memory 的持续更新问题放进更广义的“记忆巩固”语境中。公开摘录表明，作者显式借用了认知科学与记忆研究中的 consolidation framing，用来解释为什么经验在反复重写之后会出现失真、重构与误导。

从技术谱系看，本文不是否定 memory，而是在批判一类“默认持续 consolidation”的实现路线：

1. 只保留高层 textual lessons；
2. 频繁触发 memory rewrite；
3. 把抽象记忆视作对原始轨迹的充分替代。

论文提出的修正方向是：

- 原始 episodic evidence 不应被轻易丢弃；
- consolidation 需要 gate，而不是默认触发；
- heterogeneous task families 应优先分组，再做抽象。

从研究关系上说，本文更像是给 agent memory 领域加入了一条“反身性批评”：memory 不只是存储问题，也是表示保真与更新稳定性问题。
