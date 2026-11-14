---
'@openviking/react': minor
---

## en-US

Add built-in HITL sequences with Previous/Next navigation, preserved drafts and a single final submission of all answers. Disable submission for incomplete answers and invalid schedules. Text questions now require non-blank input by default; set `required: false` to retain optional-text behavior. Existing single-question submissions keep their shape; sequences return a `steps` answer.

## zh-CN

HITL 内置多步骤确认，支持上一步、下一步、草稿保留和最后一步汇总提交。答案未完成或定时值无效时禁用提交。文本题现在默认要求非空内容；原先允许空文本的题目请设置 `required: false`。单题提交结构保持不变，分步提交返回 `steps` 答案。
