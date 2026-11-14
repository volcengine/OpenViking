# 任务：GitHub PR 逐条分诊（triage）

你在为仓库 `{repo}` 的一批 Pull Request 做结构化分诊。只允许依据输入中给出的 PR 标题、正文、标签与状态做判断，**禁止使用任何外部知识或猜测**；信息不足时按低严重度处理。

## 输出要求

只输出 JSON 对象，不要输出任何解释文字，结构如下：

```json
{{
  "items": [
    {{
      "number": 123,
      "topic": "gateway",
      "severity": "high",
      "summary_zh": "一句话中文摘要，说明这个 PR 实际做了什么，不要逐字翻译或复述标题",
      "impact": "一句话中文影响面判断：影响哪个组件/链路/用户，什么情况下会感知到",
      "recommended_action": "给维护者/评审者的具体下一步；没有可空字符串"
    }}
  ]
}}
```

## 字段规则

- `number`：必须与输入 PR 的 number 完全一致，禁止编造输入中不存在的编号。
- `topic`：小写英文主题词，从 PR 实际内容归纳，例如 `gateway`、`storage`、`sdk`、`ci`、`memory`、`session`、`ui`、`security`、`docs`；同类 PR 必须使用同一个 topic 词。
- `severity`：只能是 `high`、`medium`、`low`。
  - high：安全修复、数据损坏/丢失风险、核心链路不可用、破坏性 API 变更；
  - medium：功能缺陷修复、重要特性、跨模块重构；
  - low：文档、样式、CI、依赖升级、小型重构。
- `summary_zh`、`impact`、`recommended_action` 一律使用简体中文，每条不超过 80 个汉字。
- 输出条目数必须与输入 PR 数一致，每个输入 PR 恰好对应一条输出。

## 待分诊 PR（共 {pr_count} 条）

```json
{payload}
```
