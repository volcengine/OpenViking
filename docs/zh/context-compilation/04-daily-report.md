# 示例：日报

把带时间戳的对话记录、Agent 会话、IM 消息、协作文档、会议纪要、任务记录等材料，编译成简洁、有出处的**日报**：每天一页。

Skill 源码：[examples/compile/ov-compile-skills/daily-report](https://github.com/volcengine/OpenViking/tree/main/examples/compile/ov-compile-skills/daily-report)

## 第一步：准备来源

日报的来源通常是已经在 OpenViking 里的会话、消息或文档。如果要从本地导入一批记录：

```bash
ov add-resource ./work-logs --to viking://resources/work-logs --wait
ov ls -r viking://resources/work-logs
```

## 第二步：添加 Skill

```bash
ov add-skill examples/compile/ov-compile-skills/daily-report --wait
ov skills list
# → viking://agent/skills/daily-report  （或 viking://user/<你>/skills/daily-report）
```

## 第三步：执行编译

在 `--reason` 里说清楚**日期、时区、报告对象和侧重点**，Skill 会据此定位和取舍：

```bash
ov compile \
  --from viking://resources/work-logs \
  --to viking://resources/daily-report \
  --skill viking://agent/skills/daily-report \
  --reason "生成 2026-08-20 的日报，聚焦我的工作产出与决策" \
  --wait
```

一次生成多天，把日期范围写进 `--reason` 即可（每天仍是独立一页）：

```bash
ov compile \
  --from viking://resources/work-logs \
  --to viking://resources/daily-report \
  --skill viking://agent/skills/daily-report \
  --reason "生成 2026-08-18 至 2026-08-20 每天一份日报" \
  --wait
```

去掉 `--wait` 会立刻返回 `task_id`：

```bash
ov task status cmp_01abc      # 查看进度与最终结果
ov task cancel cmp_01abc      # 协作式取消
```

## 第四步：看看产物

日报是纯 Markdown，直接读即可：

```bash
ov tree viking://resources/daily-report
ov read viking://resources/daily-report/2026-08-20.md
```


## 相关文档

- [上下文编译概览](./01-overview.md)
- [知识蒸馏示例](./05-knowledge-distillation.md)
- [VikingBot API → compile()](../api/24-vikingbot.md#compile)
