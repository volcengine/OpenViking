# 示例：知识蒸馏

把一个或多个知识库、文档集合**蒸馏**成按主题组织、有出处的高层次知识：跨来源的发现、趋势、变化、驱动因素、对比、影响和不确定性。

典型用途：蒸馏一个知识库、对比多个集合，如从一叠财报里推导出「跨报告期的变化」这类高阶洞察。

产物是一棵按主题组织的浅层工件树，每个主题目录是一个持久的语义领域，每一页是一条独立有用的高层次结论：

```text
revenue-quality/
  growth-shifted-from-volume-to-pricing.md
  overseas-growth-offset-domestic-slowdown.md
profitability/
  margin-recovered-but-cash-conversion-weakened.md
risk/
  customer-concentration-increased.md
```

> 上面只是形态示例——真实的主题和结论由你给的领域决定。

Skill 源码：[examples/compile/ov-compile-skills/knowledge-distillation](https://github.com/volcengine/OpenViking/tree/main/examples/compile/ov-compile-skills/knowledge-distillation)

## 第一步：准备来源

```bash
ov add-resource ./finance-reports --to viking://resources/finance-reports --wait
ov ls -r viking://resources/finance-reports
```

## 第二步：添加 Skill

```bash
ov add-skill examples/compile/ov-compile-skills/knowledge-distillation --wait
ov skills list
# → viking://agent/skills/knowledge-distillation  （或 viking://user/<user_name>/skills/knowledge-distillation）
```

## 第三步：执行编译

在 `--reason` 里说清**分析问题、对比维度、基线和范围**——这直接决定蒸馏的方向：

```bash
ov compile \
  --from viking://resources/finance-reports \
  --to viking://resources/finance-insights \
  --skill viking://agent/skills/knowledge-distillation \
  --reason "对比近三年财报，找到营收质量、盈利能力和风险的变化及驱动因素" \
  --wait
```

`--from` 可以传多个来源，用于跨知识库对比：

```bash
ov compile \
  --from viking://resources/finance-2024,viking://resources/finance-2025 \
  --to viking://resources/finance-insights \
  --skill viking://agent/skills/knowledge-distillation \
  --reason "对比两个年度知识库，找出关键指标的变化与结构性差异" \
  --wait
```

去掉 `--wait` 会立刻返回 `task_id`：

```bash
ov task status cmp_01abc      # 查看进度与最终结果
ov task cancel cmp_01abc      # 协作式取消
```

## 第四步：看看产物

先看主题树，再钻进具体结论页：

```bash
ov tree viking://resources/finance-insights
ov read viking://resources/finance-insights/revenue-quality/growth-shifted-from-volume-to-pricing.md
```

## 相关文档

- [上下文编译概览](./01-overview.md)
- [日报示例](./04-daily-report.md)
- [VikingBot API → compile()](../api/24-vikingbot.md#compile)
