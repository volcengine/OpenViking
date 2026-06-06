# Useful Memories Become Faulty When Continuously Updated by LLMs

## Evidence status

当前目录保存的是 **ARA draft 级证据汇总**，不是完整逐图逐表的论文证据库。

## Extracted evidence currently available

1. 论文区分 episodic traces 与 consolidated abstractions。
2. 持续 consolidation 会导致 utility 先升后降，并可能低于 no-memory baseline。
3. regression 被归因到 consolidation step，而不只是原始经验本身。
4. 在 ARC-AGI Stream 中，agent 默认偏向保留 raw episodes。
5. episodic-only 控制组保持竞争力。
6. 在项目页提供的三种离线策略比较中，Static-Group 优于 Static-All 与 Stream Update。
7. 可检索摘录显示：即使从 ground-truth solutions 出发做 consolidation，强模型仍可能明显回退。

## Missing evidence

由于当前环境无法直接下载 PDF 并逐页解析，以下内容尚未归档：

- 所有编号 Figure 的截图与结构化描述；
- 所有编号 Table 的截图与转写；
- appendix 的逐节抽取；
- 完整数值结果矩阵；
- figure-level 视觉证据与低/高置信度标注。

## Recommended next step

若后续环境允许直接拉取 PDF，可补全：

- `evidence/figures/figureN.{png,md}`
- `evidence/tables/tableN.{png,md}`
- appendix 对应的补充 evidence 文件
