# 实时上下文

项目进度、待办数量和服务状态会随时变化。实时上下文（Live Context）让 Agent 通过一个固定的 `viking://` 路径，在读取时查询数据源，取得当次调用的结果。

OpenViking 将远程 MCP 工具与固定参数绑定为一个可检索的 Markdown 节点。Agent 找到节点后，像读取资料一样完整读取它，OpenViking 就会调用对应工具。本节以 Lighthouse 演示项目为例：挂载待处理反馈查询，将数量从 3 条改为 1 条，再读取同一路径查看变化。

## 与直接调用 MCP 的区别

两种方式都能查询最新数据，区别在于 Agent 如何找到和使用它：

| | Agent 直接调用 MCP | 通过 OpenViking 挂载 |
| --- | --- | --- |
| 查询方式 | 选择工具，按工具定义填写参数并调用 | 检索业务含义明确的节点，再完整读取 |
| 本例中的操作 | 调用 `project_status`，传入 `project=Lighthouse` | 找到“Lighthouse 待处理反馈”节点，读取其 URI |
| 接入与维护 | 在 Agent 宿主中配置上游 MCP 连接 | 在 OpenViking 中管理上游连接、工具绑定和固定参数；Agent 使用 OpenViking 的检索与读取入口 |

挂载让这类查询与普通资料使用同一套检索和读取方式，固定参数由节点复用。配置工作在挂载时完成，Agent 使用节点时无需再构造这些参数。只有少量工具、查询参数经常变化时，直接调用 MCP 通常更直接；高频且参数稳定的查询适合挂载。

## 准备环境

- **OpenViking 服务和 CLI**：按照[快速开始](02-quickstart.md)完成连接配置。两者都需要支持实时上下文；运行 `ov mcps --help` 确认 CLI 提供该命令。注册和挂载会使用服务端的模型及索引配置，可能产生模型调用费用。
- **Python 和 uv**：样例需要 Python 3.10+ 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)。运行 `python3 --version` 和 `uv --version` 检查；`uv run` 会安装脚本声明的 MCP 依赖。
- **访问权限**：使用同一个 OpenViking 用户完成操作，并确认能够在 `viking://resources/onboarding-live-context-v1/` 下创建节点。

开始前，确认 CLI 当前连接的服务和身份，并检查 MCP 管理接口是否可用：

```bash
ov health
ov mcps list
```

`health` 应返回健康状态；启用 API Key 鉴权时，还应显示当前 Account、User 和角色。`mcps list` 应正常返回列表，空列表也可以继续。使用能够访问目标目录的 User/Admin Key；`api_key` 模式下，Root Key 不用于本节的数据操作。若返回 404、缺少 API Key 或身份错误，先通过 `ov config` 检查并选择正确的连接。

本例适用于 OpenViking 服务直接运行在本机的环境，MCP 样例也在本机运行。使用容器或远程 OpenViking 服务时，`127.0.0.1` 指向服务自身的运行环境；需要准备服务端可访问的 Streamable HTTP MCP 地址，接入方法见[实时上下文接入与编排](../guides/24-live-context-arrangement.md)。

## 启动样例数据源

下载[实时上下文样例包](/quickstart/live-context-demo-v1.zip)，在终端进入下载目录。解压后启动样例，包内的 `status.json` 已将虚构的待处理反馈数量设为 3 条：

```bash
unzip live-context-demo-v1.zip
cd live-context-demo
uv run live-context-demo.py
```

保持这个终端运行。脚本在 `http://127.0.0.1:8765/mcp` 提供 `project_status` 工具，每次调用都会重新读取同目录中的 `status.json`。工具只查询数据，不会修改反馈。

如果 8765 端口已被占用，可以使用 `uv run live-context-demo.py --port 18765` 启动，并将后续 `validate` 和 `add` 命令中的端口一并改为 `18765`。

另开一个终端，进入同一目录，验证 OpenViking 服务能连接样例并发现工具：

```bash
ov mcps validate '{"mcpServers":{"onboarding-live-v1":{"url":"http://127.0.0.1:8765/mcp"}}}'
```

确认验证通过，发现的工具数量为 1 后继续。如果连接失败，先检查样例进程是否运行，以及 OpenViking 服务是否直接运行在本机。

## 注册并挂载节点

将数据源注册到当前用户的工具目录，等待工具目录处理完成：

```bash
ov mcps add '{"mcpServers":{"onboarding-live-v1":{"url":"http://127.0.0.1:8765/mcp"}}}' --wait
ov mcps show onboarding-live-v1
```

确认工具列表中有 `project_status`，参数包含字符串类型的 `project`。`viking://~/tools/` 表示当前用户的工具目录。重复体验时，如果提示同名 Server 已存在，先用 `show` 确认地址和工具仍与本例一致，再继续。

把这个工具挂载到演示目录，固定查询 Lighthouse 项目：

```bash
ov mcps mount \
  viking://~/tools/onboarding-live-v1/project_status.json \
  viking://resources/onboarding-live-context-v1/status.md \
  --title "Lighthouse 待处理反馈" \
  --description "查询 Lighthouse 演示项目当前的待处理反馈数量。" \
  --args '{"project":"Lighthouse"}' \
  --wait
```

挂载成功后，查看绑定关系：

```bash
ov mcps mounts --mcp onboarding-live-v1
```

结果应包含 `status.md` 的 URI、`project_status` 工具和 `project=Lighthouse` 参数。再次挂载到同一 URI 会更新绑定；重复体验时请确认这是本次演示节点。

## 读取并观察变化

先按业务问题检索节点，无需指定上游工具名：

```bash
ov find "Lighthouse 项目现在有多少条待处理反馈？" \
  --uri viking://resources/onboarding-live-context-v1
```

在结果中找到 `status.md`，核对标题和描述是否对应 Lighthouse 待处理反馈。检索找到的是查询入口，当前数量需要完整读取后才能获得：

```bash
ov read viking://resources/onboarding-live-context-v1/status.md
```

结果应包含以下字段：

```json
{"project":"Lighthouse","pending_feedback":3}
```

在第二个终端中修改数据源，再次读取同一 URI：

```bash
printf '%s\n' '{"pending_feedback":1}' > status.json
ov read viking://resources/onboarding-live-context-v1/status.md
```

此时 `pending_feedback` 应变为 `1`。本次只修改了样例数据源，未重新导入资料、同步工具或修改挂载；读取时，OpenViking 再次调用了同一个工具。

“实时”表示读取时发起查询，数据的新旧程度取决于上游系统。它不会让数据源主动推送变化，也不会自动更新 Agent 已经读过的回答。

## 让 Agent 使用节点

如果已经完成 [Agent 接入](07-agent-onboarding.md)，可以让 Agent 自己检索并读取节点。Agent 应连接同一服务，并使用能够访问该节点和当前用户工具目录的身份。使用一个未直接连接样例 MCP Server 的 Agent 会话，输入：

```text
Lighthouse 项目现在有多少条待处理反馈？
请在 OpenViking 的 viking://resources/onboarding-live-context-v1/ 下
检索相关上下文，完整读取找到的节点后回答，并引用来源 URI。
如果读取失败，请说明原因，不要沿用之前的数量。
```

检查调用记录：Agent 应先检索到 `status.md`，再完整读取它，最后回答 1 条待处理反馈并引用节点 URI。提示词只给了业务问题和检索范围，没有提供上游工具名、参数格式或节点的完整路径；这些绑定由 OpenViking 处理。

数量从 3 变为 1 展示了读取时查询数据源；Agent 通过检索找到节点，再用统一的读取入口取得结果，展示了挂载后的使用方式。

## 检查结果

| 检查项 | 预期结果 |
| --- | --- |
| 工具可用 | `show` 中有 `project_status` 及其参数定义 |
| 挂载正确 | `mounts` 中的节点 URI、工具和项目参数一致 |
| 节点可发现 | 按业务问题检索，结果中包含 `status.md` |
| 首次读取 | 返回 Lighthouse 项目，待处理反馈为 3 条 |
| 数据源更新后读取 | 同一 URI 返回 1 条，无需重新挂载 |
| Agent 使用（可选） | 无需直接接入样例 MCP，记录中有检索及完整读取，回答包含最新数量和来源 URI |

## 常见问题

| 现象 | 处理方法 |
| --- | --- |
| CLI 不识别 `mcps`，或服务没有对应接口 | 更新到支持实时上下文的 CLI 和服务端；安装入口见 [CLI 入门](05-cli-setup.md) |
| 验证或读取连接失败 | 保持样例进程运行，确认从 OpenViking 服务端能访问 MCP 地址 |
| 读取后仍为 3 条 | 确认修改的是样例脚本同目录的 `status.json`，再发起一次读取 |
| 检索没有找到节点 | 先用 `mounts` 确认挂载，再检查挂载处理结果、索引状态和检索范围；直接读取成功不代表索引已可用 |
| 返回节点描述文件 | 使用不带分页参数的 `ov read`；分页读取或 API 的 `raw=true` 不执行上游工具 |
| 提示工具不是只读工具 | 本例工具已声明只读；其他工具若明确标为非只读或破坏性，不能通过 `read` 执行，需按[工具调用接口](../api/26-mcp-live-context.md)显式调用 |
| Agent 回答仍使用旧数量 | 检查是否真的执行了本次完整读取，并确认服务和用户身份一致 |

`find`、`search` 和目录浏览不会调用实时节点。上游工具返回错误时，读取也会报错，应先排查数据源。

## 继续使用

你可以把这个方式用于项目进度、工单数量或服务状态等高频查询。固定参数的查询适合挂载为实时上下文；参数随问题变化的查询，可以通过工具发现与调用完成。Studio 编排、鉴权、工具变更同步和 Context as Code 见[实时上下文接入与编排](../guides/24-live-context-arrangement.md)。

体验结束后，先卸载本次节点，再删除样例 Server 注册：

```bash
ov mcps unmount viking://resources/onboarding-live-context-v1/status.md --wait
ov mcps remove onboarding-live-v1
```

回到运行样例的终端，按 `Ctrl+C` 停止数据源。
