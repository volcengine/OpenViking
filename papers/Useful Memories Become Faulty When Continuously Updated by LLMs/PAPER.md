# Useful Memories Become Faulty When Continuously Updated by LLMs

> ARA draft（中文）
>
> 说明：本稿基于论文公开摘要页、可检索到的 PDF 摘录与项目页整理而成；当前环境未能直接下载 PDF 并逐页完成表格/图片证据抽取，因此这是结构化分析草稿，不是完整 Seal Level 1 工件。

## Frontmatter

- **title**: Useful Memories Become Faulty When Continuously Updated by LLMs
- **authors**: Dylan Zhang; Yanshan Lin; Zhengkun Wu; Yihang Sun; Bingxuan Li; Dianqi Li; Hao Peng
- **year**: 2026
- **venue**: arXiv preprint, cs.AI
- **doi**: 10.48550/arXiv.2605.12978
- **keywords**: agentic memory, episodic traces, consolidated abstractions, faulty memory, memory consolidation, continual update
- **claims_summary**:
  1. 持续在线更新的文本记忆会逐渐退化，甚至低于 no-memory baseline。
  2. 问题根源在于 consolidation 过程本身，而不只是原始经验质量。
  3. 保留 raw episodic traces、限制默认 consolidation、更稳定的分组抽象（如 Static-Group）更稳健。

## Abstract Summary

论文区分两类记忆：一类是原始轨迹形式的 episodic traces，另一类是跨 episode 提炼出来的 consolidated abstractions。作者指出，许多 agentic memory 方法依赖后者：让 LLM 不断把过去轨迹改写进文本 memory bank，并希望实现“无参数自我提升”。但实验显示，随着 consolidation 持续发生，memory utility 会先升后降，甚至跌破 no-memory baseline；因此，有用记忆会在持续更新中变成 faulty memories。

## Layer Index

- `logic/problem.md`
- `logic/claims.md`
- `logic/concepts.md`
- `logic/experiments.md`
- `logic/related_work.md`
- `logic/solution/constraints.md`
- `logic/solution/memory_design.md`
- `src/environment.md`
- `trace/exploration_tree.yaml`
- `evidence/README.md`
