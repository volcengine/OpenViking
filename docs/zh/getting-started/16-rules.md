# 规则（Beta）

::: warning Beta 提示
规则功能目前处于 Beta 阶段，本页介绍的接口、参数和使用流程在后续版本中可能发生不兼容调整。升级前，请查阅版本说明和最新文档，并在测试环境中验证现有接入流程。
:::

Rule 用来保存 Agent 在特定条件下需要遵守的明确要求，例如发布前的检查项、代码规范中的禁止项。OpenViking 将这些要求编译为规则，按当前任务召回；Agent 再结合原文证据，检查行动或回答是否满足要求。

本次体验为 Lighthouse 演示项目保存一条发布规则，再检查两份发布说明。完成后，你可以看到规则原文、召回结果，以及补齐材料前后的检查差异。

## 适用场景

| 场景 | 可以保存的规则 | Agent 如何使用 |
| --- | --- | --- |
| 发布检查 | 项目发布说明必须包含回滚步骤 | 发布前核对说明，指出缺少的内容 |
| 代码审查 | 团队禁止在业务代码中记录访问密钥 | 对照变更内容检查，并标注相关代码 |
| 协议核查 | 用户提供的协议义务、适用条件与例外 | 逐条核对材料，区分已满足、违反和证据不足 |

Skill 说明“怎样做”，Rule 说明“在什么条件下需要遵守哪些要求”。明确的义务和禁止项适合保存为 Rule；个人偏好和普通建议可以继续放在记忆中。

## 准备环境

准备已启用 Rule 功能的 OpenViking 服务，以及 Python 环境；安装入口见[快速开始](02-quickstart.md#准备工作)。服务端需要可用的 Embedding 和 Compiler 模型，Compiler 默认复用 VLM，配置见 [Rules 配置](../configuration/01-server.md#rules)。编译和检索会调用模型，并将演示规则保存在当前用户的规则库中，适合使用测试用户体验。

在 Python 环境中安装 SDK：

```bash
python -m pip install --upgrade openviking-sdk
```

本例使用支持 `rule_compile()` 和 `rule_search()` 的 Python HTTP SDK；服务端与 SDK 都需要包含 Rule（Beta）能力。使用托管服务时，可先向服务提供方确认支持情况。连接与身份配置见 [API 约定](../api/01-overview.md)。

将下面的 Python 片段依次放入同一个 `rule_demo.py` 文件，填写服务地址和当前用户的 API Key；本地未启用鉴权时可省略 `api_key`。

## 保存规则

使用一条仅针对演示项目的要求，明确触发条件和需要完成的检查：

```python
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
task = client.rule_compile(
    instruction="检查 Lighthouse 演示项目的发布说明时，发布说明必须包含回滚步骤。",
)
print("Compile task:", task["task_id"])
```

服务会返回后台任务 ID。接下来等待编译和关联索引任务完成，再读取实际生成的规则：

```python
import time


def wait_for_task(task_id, timeout=300):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = client.get_task(task_id)
        if current is None:
            raise RuntimeError(f"Task not found: {task_id}")
        if current["status"] == "completed":
            return current
        if current["status"] in {"failed", "cancelled"}:
            raise RuntimeError(current)
        time.sleep(1)
    raise TimeoutError(f"Task still running: {task_id}")


result = wait_for_task(task["task_id"])["result"]
print("Operations:", result["operations"])
write_task_id = result.get("write_result", {}).get("task_id")
if write_task_id:
    wait_for_task(write_task_id)
for uri in result["written_uris"]:
    print(uri)
    print(client.read(uri))
```

检查规则是否保留了“Lighthouse 演示项目”“检查发布说明时”和“必须包含回滚步骤”。重复提交可能得到 `NOOP`，表示已有规则覆盖输入，可以继续检索；`UNCERTAIN` 或 `REJECT` 会给出未写入原因，先按原因调整输入。等待超时后，可用已打印的任务 ID 继续查询进度。

## 按任务召回

以下是固定的虚构样例：Lighthouse 演示项目的发布说明全文为“本次发布新增导出进度提示。”将这段证据和检查目标一起提交：

```python
matches = client.rule_search(
    task_context=(
        "检查 Lighthouse 演示项目的发布说明。"
        "已读取发布说明全文：本次发布新增导出进度提示。"
        "计划核对发布要求，并列出需要补充的内容。"
    ),
)
print(matches["rendered"])
print("Warning:", matches.get("warning"))
print("Scenario selection:", matches.get("scenario_selection"))
```

保存文件后运行：

```bash
python rule_demo.py
```

在输出中找到规则正文及 URI，核对它是否包含刚才的回滚要求。`rendered` 是可交给 Agent 的完整规则文本；搜索负责找到可能适用的规则，具体是否满足要求由 Agent 对照材料检查。

## 核对检查结果

把上一步完整的 `rendered` 输出附在下面的提示词后，交给你的 Agent。两份说明均为演示材料，Agent 只需阅读并返回检查结果：

```text
请依据附后的规则，分别检查 Lighthouse 演示项目的两份发布说明。

版本 A 全文：本次发布新增导出进度提示。
版本 B 全文：本次发布新增导出进度提示。回滚步骤：停止新版本服务，
切换到上一版本镜像，使用上一版本配置启动服务，并检查健康接口。

请逐条核对规则的适用条件和例外，引用规则 URI 与发布说明原文，
分别说明要求是已满足、违反还是证据不足，并列出需要补充的内容。
本次只做文本检查。

以下是检索得到的完整规则：
（粘贴 rendered 输出）
```

| 核对项 | 预期观察 |
| --- | --- |
| 规则适用 | 两份材料都属于 Lighthouse 演示项目的发布说明 |
| 版本 A | 缺少回滚步骤，Agent 指出需要补充 |
| 版本 B | 包含回滚步骤，满足这条规则；步骤是否实际可行还需部署验证 |
| 结论可追溯 | 回答引用了规则 URI 和对应的说明原文 |

这张表是样例的验收标准，实际运行结果以你的模型输出为准。没有召回规则时，可先检查编译与索引任务、用户身份和任务描述。出现 `warning` 时，结合提示检查模型配置，并让 Agent 逐条核对条件；空结果或放行分数都需要结合材料继续检查。

## 继续使用

接入 [VikingBot](11-vikingbot.md) 后，可以让它主动调用 `openviking_rule_search` 工具完成召回。当前 Beta 由用户或 Agent 主动触发编译与搜索，普通会话提交不会自动生成 Rule。

需要批量导入规范或接入自己的 Agent 时，继续阅读[规则校验](../workflows/02-rule-validation.md)；暂停、更新和归档规则的方法见[规则生命周期](../concepts/17-rule-scenarios.md#生命周期)。
