# Case Identity 精确召回与 Situation 语义回退

## 目标

统一 `experience_loader` 的召回行为：

- 评测运行提供稳定 Case 身份且该 Case 已有关联经验时，精确返回同 Case 经验。
- 当前 Case 不存在或没有关联经验时，使用当前 Situation 召回语义相近的 Case。
- 线上运行没有 Case 身份时，仅使用 Situation 语义召回。

Case 身份不参与向量查询；Experience 继续保持通用，不写入评测题号。

## 工具接口

公开工具保持单一入口，并增加一个可选参数：

```python
search_experience(
    situation: str,
    case_identity: str | None = None,
    limit: int = 2,
)
```

`situation` 只包含用户目标、操作对象、范围和显式约束。`case_identity` 是运行上下文明确提供的稳定身份；Agent 不得推断或编造它。

Case memory 使用现有 `task_signature` 字段保存并校验该身份，经验关联仍由 Case 的 `Linked Experiences` 表达。

## 运行时提供身份

通用 Skill 将 `case_identity` 视为可选信息：有则原样传入，没有则省略。

Tau2 adapter 已能从 `Case` 构造稳定的 `task_signature`。在准备本次运行的 `experience_loader` Skill 时，adapter 把该值作为明确的运行时 Case context 告知 Agent。线上 adapter 不提供该段上下文，因此不需要伪造身份，也不需要修改线上调用方。

## 召回流程

1. `case_identity` 非空时，按 Case memory 的 `task_signature` 做精确匹配。
2. 精确 Case 存在且 `Linked Experiences` 非空时，只返回这些经验，不补充其他语义 Case。
3. 精确 Case 不存在或没有关联经验时，使用 `situation` 搜索 Case，返回语义候选所关联的经验。
4. `case_identity` 为空时，直接执行第 3 步。
5. 所有返回的 Experience URI 去重；精确匹配失败不是错误，不阻止语义回退。

精确匹配必须校验 `task_signature`，不能只依赖文件名或向量相似度。实现应复用现有 Case identity 解析和校验能力，避免形成第二套身份规则。

## 返回与可观测性

返回格式继续包含 Case 和 Experience 候选，并为诊断增加非业务字段：

- `match_type`: `exact_case` 或 `semantic`
- `case_identity`: 仅在调用方提供时回显

Trace 记录选择的召回路径、精确 Case 是否存在、是否有关联经验，以及语义回退原因；不记录额外的完整经验正文。

## 验证

- 同 `task_signature` 且有关联经验：只返回精确 Case 的经验，不调用语义搜索。
- 同 `task_signature` 但没有关联经验：执行 Situation 搜索。
- 找不到 `task_signature`：执行 Situation 搜索。
- 未提供 `case_identity`：执行 Situation 搜索。
- 精确与语义候选存在重复 URI：最终只返回一次。
- Tau2 Agent 能从运行时 Skill context 读取并原样传入 `case_identity`；线上 Skill 没有该上下文时仍能正常调用。

