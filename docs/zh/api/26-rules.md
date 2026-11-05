# 规则（Beta）

规则 API 将用户提供的强约束编译为持久 Rule，并按任务上下文召回适用规则。当前提供 Python HTTP SDK 和 HTTP 接口；没有对应的专用 CLI、TypeScript SDK 或 Go SDK 方法。VikingBot 提供[规则搜索工具（Beta）](../guides/17-vikingbot.md)。

编译和搜索使用当前认证用户的规则库 `viking://user/<user_id>/memories/rules/`。规则概念见[规则（Beta）](../concepts/17-rule-scenarios.md)，完整操作流程见[规则校验（Beta）](../workflows/02-rule-validation.md)。

## API 参考

### rule_compile()

#### 1. 用途与前置条件

提交规则文本或来源材料，后台编译后新增、更新或按用户明确指令变更规则状态。需配置可用的 Compiler 模型；默认复用服务端 VLM，也可设置 `rules.compiler`。

来源可以是已导入的资源，也可以是待导入的文件、目录或 URL。新来源先走资源导入流程，再提炼约束。编译不会从普通会话提交中自动触发。

#### 2. 接口和参数说明

```text
POST /api/v1/rules/compile
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `instruction` | string | 与 `sources` 至少提供一项 | `null` | 规则文本或明确的变更要求；最多 64,000 字符。没有来源时，去除首尾空白后必须非空 |
| `sources` | array | 与 `instruction` 至少提供一项 | `[]` | 最多 16 个来源；Python SDK 放在 `options["sources"]` 中 |

每个 HTTP 来源只能使用以下一种形式：

| 来源 | 格式 | 说明 |
| --- | --- | --- |
| 已有资源 | `{"resource_uri": "viking://resources/standards"}` | 直接读取资源正文，不重复导入 |
| 新远程来源 | `{"path": "https://example.com/standards.md"}` | 复用资源导入请求；可用字段见[资源 API](./02-resources.md) |
| 新本地来源 | `{"temp_file_id": "upload-id", "source_name": "standards.md"}` | 先通过资源临时上传接口上传；目录先打包为 ZIP |

Python SDK 还支持来源字符串：本地文件或目录路径、URL、`viking://` 资源 URI；会自动上传本地来源并转换为 HTTP 对象。

编译器生成变更计划，调用者不直接提交 Mutation：

| 操作 | 行为 |
| --- | --- |
| `NEW` | 创建 active Rule，复用或新建 Scenario |
| `UPDATE` | 更新单条规则，保留标识和 active/paused 状态 |
| `REPLACE` | 用户明确完整替代单条旧规则时，创建新标识并归档旧规则 |
| `NOOP` | 已有 active Rule 完整覆盖输入，不写入 |
| `UNCERTAIN` / `REJECT` | 记录歧义或拒绝原因，不写入 Rule |
| `PAUSE` | 用户明确暂停或禁用 active Rule |
| `RESTORE` | 用户明确恢复 paused Rule |
| `ARCHIVE` | 用户明确删除、丢弃或移入回收站；授权必须来自 `instruction` |

当前编译候选只包含 active 和 paused；没有恢复 archived 的公开入口，也没有按 ID 直接提交生命周期操作的专用接口。

#### 3. 使用示例

**Python SDK (HTTP)**

```python
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
task = client.rule_compile(
    instruction="仓库声明自己是开源项目时，必须提供完整的项目许可证文本。",
)
print(task["task_id"])

# 单次查询；完整等待与索引检查见规则校验教程。
print(client.get_task(task["task_id"]))
```

**HTTP API**

```bash
curl -X POST http://localhost:1933/api/v1/rules/compile \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{
    "instruction": "仓库声明自己是开源项目时，必须提供完整的项目许可证文本。"
  }'
```

#### 4. 响应契约与错误处理

HTTP 返回 **202 Accepted** 和后台任务记录。Python SDK 返回外层 `result` 中的任务对象。以下示例省略时间和元数据：

```json
{
  "status": "ok",
  "result": {
    "task_id": "rule_compile_example",
    "task_type": "rule_compile",
    "status": "pending",
    "resource_id": "viking://user/alice/memories/rules",
    "stage": "queued",
    "result": null,
    "error": null
  }
}
```

收到任务不表示编译完成。通过[后台任务 API](./17-tasks.md)查询至 `completed`，处理 `failed`、`cancelled`；任务刚返回时也可能已进入 `running`。完成后的 `task.result` 包含：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `rules_root` | string | 当前用户规则库 URI |
| `source_uris` | array | 本次来源资源 URI；纯文本输入时可为空 |
| `plan_reason` | string | 编译计划说明 |
| `batch_count` | integer | 输入编译批次数 |
| `operations` | array | 实际变更或未写入原因；字段随操作类型变化 |
| `written_uris` | array | 本次写入的 Rule 和 Scenario URI |
| `write_result` | object | 批量写入结果；有写入时可包含独立 `task_id`，用于跟踪索引处理 |

任务完成结果示例（省略任务对象的其他字段及写入统计）：

```json
{
  "status": "ok",
  "result": {
    "task_id": "rule_compile_example",
    "task_type": "rule_compile",
    "status": "completed",
    "result": {
      "rules_root": "viking://user/alice/memories/rules",
      "source_uris": [],
      "plan_reason": "创建项目许可证要求。",
      "batch_count": 1,
      "operations": [
        {
          "operation": "NEW",
          "atomic_id": "project-license",
          "uri": "viking://user/alice/memories/rules/repository-license/project-license.md"
        }
      ],
      "written_uris": [
        "viking://user/alice/memories/rules/repository-license/abstract.md",
        "viking://user/alice/memories/rules/repository-license/project-license.md"
      ],
      "write_result": {"task_id": "write_example"}
    },
    "error": null
  }
}
```

先检查实际操作和生成内容，再等待 `write_result.task_id` 对应任务完成后检索。请求结构或长度不合法返回 HTTP 422；后台解析、模型或校验失败记录在任务 `error` 中，规则编译失败以 `RULE_COMPILE_FAILED` 标识。

---

### rule_search()

#### 1. 用途与前置条件

根据当前任务召回 active 规则，返回完整约束及适用性信息。应先确认规则编译和关联索引任务完成，并以相同用户身份搜索。

任务上下文应包含目标、对象、计划动作和已知事实。搜索判定适用性，Agent 负责核验事实和是否违反规则。

#### 2. 接口和参数说明

```text
POST /api/v1/rules/search
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `task_context` | string | 是 | — | 非空任务上下文，最多 64,000 字符 |
| `rule_probability_threshold` | number | 否 | `0.65` | `0`–`1`，包含边界；返回分数大于等于该值的规则。SDK 放在 `options` 中 |

公开接口没有 `limit`、`scenario_limit` 或 `target_uri` 参数。搜索先语义召回 Scenario，再过滤无关场景并读取其中的 active Rule。

#### 3. 使用示例

**Python SDK (HTTP)**

```python
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
result = client.rule_search(
    task_context="只读核查仓库许可证；README 声明开源，计划检查 LICENSE 正文。",
    options={"rule_probability_threshold": 0.65},
)
print(result["rendered"])
print(result.get("warning"))
```

**HTTP API**

```bash
curl -X POST http://localhost:1933/api/v1/rules/search \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{
    "task_context": "只读核查仓库许可证；README 声明开源，计划检查 LICENSE 正文。",
    "rule_probability_threshold": 0.65
  }'
```

#### 4. 响应契约与错误处理

HTTP 返回 **200 OK** 和结果包；Python SDK 直接返回外层 `result`。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `rules` | array | 命中规则；每项包含 `atomic_id`、`revision`、`scenario_id`、`probability`、`uri`、`content` |
| `rules[].content` | string | 完整的存储 Rule Markdown，含元数据和约束；不是只有约束句子 |
| `scenarios` | array | 进入规则读取阶段的 Scenario 标识 |
| `rendered` | string | 应用步骤和完整规则块，供 Agent 使用 |
| `delivery` | object | `complete`、`rule_count`、`rendered_chars`；描述命中规则的交付完整性 |
| `scenario_selection` | object / null | Scenario 筛选信息，包括后端、放行或过滤详情及可能的 warning |
| `judge` | string，可选 | `jev_noul` 或 `pass`；无候选时可省略 |
| `warning` | string，可选 | Rule 判断不可用时的放行说明 |

无 Scenario 候选时的响应示例：

```json
{
  "status": "ok",
  "result": {
    "rules": [],
    "scenarios": [],
    "rendered": "# Applicable OpenViking Rules\n\nThese Rules are behavioral constraints.\n\nApplication protocol:\n1. Evaluate every Rule independently against concrete facts from the current task.\n2. Check the applicability boundary and exception before applying a Rule. An empty\n   applies_when means the Rule applies in every situation except its exception.\n   For a non-empty applies_when, topic similarity alone does not establish applicability.\n3. Do not infer facts that are absent.\n4. Before acting or answering, validate the planned action or answer against every\n   returned Rule. Returned Rules are mandatory when their applicability conditions hold.\n\nMatched 0 active Rule(s).\n\nNo active compiled Rule met the current retrieval thresholds.",
    "scenario_selection": null,
    "delivery": {"complete": true, "rule_count": 0, "rendered_chars": 730}
  }
}
```

即使没有命中，`rendered` 仍包含应用步骤和空匹配提示。`delivery.complete=true` 表示选中规则未被截断，不证明全库召回；空结果不能当作合规结论。

Jev 未配置或判断失败时，受影响候选放行。Rule 判断失败返回 `judge="pass"`、`probability=1.0` 和 `warning`；分数不是估算概率。Scenario 判断异常的提示位于 `scenario_selection`。Agent 必须逐条检查适用条件与例外。

空白上下文、超长文本、阈值越界或多余参数返回 HTTP 422。Rule 文件读取失败可能使请求失败；调用方应处理接口错误，不能视为无规则。通用认证和错误格式见[API 总览](./01-overview.md)。

## 相关文档

- [规则（Beta）](../concepts/17-rule-scenarios.md)
- [规则校验（Beta）](../workflows/02-rule-validation.md)
- [后台任务](./17-tasks.md)
- [服务端 Rules 配置](../configuration/01-server.md#rules)
