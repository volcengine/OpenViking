# 示例：知识蒸馏

从一个或多个知识库中提炼结论，按主题组织，并保留出处与不确定性。

例如，对比多期财报，分析营收、利润和风险随时间的变化。

输出按主题分目录，每页说明一条结论：

```text
revenue-quality/
  growth-shifted-from-volume-to-pricing.md
  overseas-growth-offset-domestic-slowdown.md
profitability/
  margin-recovered-but-cash-conversion-weakened.md
risk/
  customer-concentration-increased.md
```

> 以上仅展示目录结构。实际主题和结论由来源与指令决定，文件名概括结论。

Skill 源码：[examples/compile/ov-compile-skills/knowledge-distillation](https://github.com/volcengine/OpenViking/tree/main/examples/compile/ov-compile-skills/knowledge-distillation)

先确认[前置条件](01-overview.md#前置条件)，并在 OpenViking 仓库根目录运行以下命令。来源目录需替换为自己的资料目录。

## 第一步：准备来源

```bash
ov add-resource ./finance-reports --to viking://resources/finance-reports --wait
ov ls -r viking://resources/finance-reports
```

## 第二步：添加 Skill

```bash
ov add-skill examples/compile/ov-compile-skills/knowledge-distillation -p viking://agent/skills --wait
ov skills list
# → viking://agent/skills/knowledge-distillation
```

## 第三步：执行编译

在 `--instruction` 里说清**分析问题、对比维度、基线和范围**——这直接决定蒸馏的方向：

```bash
ov compile \
  --from viking://resources/finance-reports \
  --to viking://resources/finance-insights \
  --skill viking://agent/skills/knowledge-distillation \
  --instruction "对比近三年财报，找到营收质量、盈利能力和风险的变化及驱动因素"
```

`--from` 可以传多个来源，用于跨知识库对比：

```bash
ov compile \
  --from viking://resources/finance-2024,viking://resources/finance-2025 \
  --to viking://resources/finance-insights \
  --skill viking://agent/skills/knowledge-distillation \
  --instruction "对比两个年度知识库，找出关键指标的变化与结构性差异"
```

命令会立刻返回 `task_id`：

```bash
ov task status cmp_01abc      # 查看进度与最终结果
ov task cancel cmp_01abc      # 协作式取消
```

## 第四步：查看产物

先查看目录，再读取实际生成的结论页。下面的文件名仅为示例：

```bash
ov tree viking://resources/finance-insights
ov read viking://resources/finance-insights/revenue-quality/growth-shifted-from-volume-to-pricing.md
```

默认不生成 `index.md`；需要导航页时，在 `--instruction` 中明确要求。再次编译同一主题时，Skill 会要求更新已有分析页，并标注可能随时间变化的结论。

## 相关文档

- [上下文编译概览](./01-overview.md)
- [日报示例](./04-daily-report.md)
- [Agent Runtime API](../api/23-agent-runtime.md)
