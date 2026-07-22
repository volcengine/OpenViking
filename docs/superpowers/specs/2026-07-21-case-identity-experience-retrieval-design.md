# Case Identity 精确召回与 Situation 语义回退

## 目标

统一 `experience_loader` 的召回行为：

- 评测运行提供稳定 `task_signature` 且对应 Case 文件存在时，直接读取该 Case；即使没有关联经验，也以该 Case 作为精确结果。
- 对应 Case 文件不存在时，使用当前 Situation 召回语义相近的 Case。
- 线上运行没有 Case 身份时，仅使用 Situation 语义召回。

Case 身份不参与向量查询；Experience 继续保持通用，不写入评测题号。

## 工具接口

公开工具保持单一入口，并增加一个可选参数：

```python
search_experience(
    situation: str,
    task_signature: str | None = None,
    limit: int = 2,
)
```

`situation` 只包含用户目标、操作对象、范围和显式约束。`task_signature` 是运行上下文明确提供的稳定 Case 身份；Agent 不得推断或编造它。

Case memory 使用同名的 `task_signature` 字段保存并校验该身份，经验关联仍由 Case 的 `Linked Experiences` 表达。

## 运行时提供身份

通用 Skill 将 `task_signature` 视为可选信息：有则原样传入，没有则省略。

Tau2 adapter 已能从 `Case` 构造稳定的 `task_signature`。在准备本次运行的 `experience_loader` Skill 时，adapter 把该值作为明确的运行时 Case context 告知 Agent。线上 adapter 不提供该段上下文，因此不需要伪造身份，也不需要修改线上调用方。

## 召回流程

1. `task_signature` 非空时，定位并读取对应的 Case 文件，再校验文件内的 `task_signature`。
2. 精确 Case 文件存在时，直接返回该 Case 及其 `Linked Experiences`；关联经验为空也是有效精确结果，不执行语义搜索。
3. 精确 Case 文件不存在时，使用 `situation` 搜索 Case，返回语义候选所关联的经验。
4. `task_signature` 为空时，直接执行第 3 步。
5. 所有返回的 Experience URI 去重。精确 Case 文件不存在是正常的语义回退条件；Case 查询或读取异常则返回错误，不伪装成未命中。

精确匹配必须校验 `task_signature`，不能只依赖文件名或向量相似度。实现应复用现有 Case identity 解析和校验能力，避免形成第二套身份规则。

## 返回与可观测性

返回格式继续包含 Case 和 Experience 候选，并为诊断增加非业务字段：

- `match_type`: `exact_case` 或 `semantic`
- `task_signature`: 仅在调用方提供时回显

Trace 记录选择的召回路径、精确 Case 是否存在、是否有关联经验，以及语义回退原因；不记录额外的完整经验正文。

## 验证

- 同 `task_signature` 且有关联经验：只返回精确 Case 的经验，不调用语义搜索。
- 同 `task_signature` 但没有关联经验：返回精确 Case 和空经验列表，不调用语义搜索。
- 找不到 `task_signature` 对应的 Case 文件：执行 Situation 搜索。
- 未提供 `task_signature`：执行 Situation 搜索。
- 多个语义候选关联同一 Experience URI：最终只返回一次。
- Tau2 Agent 能从运行时 Skill context 读取并原样传入 `task_signature`；线上 Skill 没有该上下文时仍能正常调用。
