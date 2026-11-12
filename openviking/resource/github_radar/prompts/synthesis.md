# 任务：PR Radar 主题归并与综述（synthesis）

你在为仓库 `{repo}` 本期的 Pull Request 播报做主编综述。输入包含本期全部 PR 的分诊结果（含主题、严重度、中文摘要、影响面）。只允许依据输入内容做归纳，**禁止引用输入之外的 PR、编号或事实**。

## 输出要求

只输出 JSON 对象，不要输出任何解释文字，结构如下：

```json
{{
  "editorial_note": "一句话概括本期整体形状，例如以修复为主、某主题集中爆发等，简体中文，不超过 60 个汉字",
  "sections": [
    {{
      "topic": "gateway",
      "overview_zh": "该主题下这批 PR 的合并综述：共同解决什么问题、整体方向，简体中文，不超过 120 个汉字",
      "highlight_numbers": [123, 124]
    }}
  ]
}}
```

## 字段规则

- `sections`：按主题归并，`topic` 必须取自输入分诊结果中出现过的 topic 词，不得新造。
- 每个主题恰好一个 section；按主题下 high 严重度 PR 数量从多到少排序，数量相同按 PR 数排序。
- `highlight_numbers`：该主题下最值得读者优先阅读的 PR 编号，**只能引用输入中存在的 number**；每个主题 1-3 个，优先选 high 严重度。没有 high 时选最具代表性的 medium。
- `overview_zh` 要做主题级综合，不要逐条复述 PR 摘要。
- 如果输入 PR 总数很少（≤3），可以只给一个 section。
- 全部输出使用简体中文。

## 本期分诊结果（共 {pr_count} 条 PR）

```json
{payload}
```
