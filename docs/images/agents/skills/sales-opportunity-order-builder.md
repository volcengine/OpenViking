---
name: sales-opportunity-order-builder
description: 销售商机商单 Builder。帮助销售初始化个人销售知识库与商机商单跟进工作流：检查 AgentPlan APIKey、创建个人版 OpenViking 库并取得库的 OpenViking APIKey、检查/安装飞书 CLI，再引导上传飞书销售文档、查询商机商单、定时沉淀 session 记忆。用户提到销售商机、商单、客户跟进、销售知识库、OV/OpenViking 库搭建、飞书文档上传、商机复盘、销售记忆沉淀，甚至只是说"帮我建个跟进客户的库"时，都应优先使用本 skill。
---

# 销售商机商单 Builder

帮助销售快速搭建个人销售知识库与商机商单跟进工作流。先完成环境初始化
（AgentPlan APIKey → 创建 OV 库 → 取得库凭据 → 飞书 CLI），再进入三类业务场景
（上传资料 / 查询商机 / session 沉淀）。

这是一个 **Builder**：优先帮用户把环境搭好、把工作流跑通，而不是直接跳到商机
问答。某项依赖缺失时，给出明确的下一步并暂停依赖它的后续操作，不要伪造结果。

## 关键概念：两套系统、两个 APIKey

OpenViking 相关操作分属**两个不同的面**，各用各的 Key，混用是本 skill 最常见的
故障来源：

| | 控制面（管库） | 数据面（管数据） |
|---|---|---|
| 做什么 | 创建/查询/删除 OV 库，获取库的凭据 | 向库里上传文档、检索、写记忆 |
| 用什么工具 | OpenViking **控制面** MCP（`mcp-server-openviking-controlplane`，工具：`list_collections` / `create_collection` / `get_collection` / `get_usage` / `get_collection_api_key`），或同包 CLI `ov-cp` | OpenViking **数据面** MCP / `ov` CLI（add_resource / search / remember 等） |
| 用哪个 Key | **AgentPlan APIKey**（环境变量 `AGENTPLAN_API_KEY`，Bearer） | **OpenViking APIKey**（每个库一把，创建后单独获取） |

两个 Key 的边界：

- **AgentPlan APIKey**：新建 OV 库、联网、数据集查询。拿它读写库数据会失败。
- **OpenViking APIKey**：读写某一个 OV 库的数据。拿它建库会失败。

如果发现读写库数据时用的是 AgentPlan APIKey（或反过来），立即停止并纠正。

## 总体原则

- 按顺序完成前置检查：AgentPlan APIKey → 控制面能力 → 创建/选择 OV 库 →
  获取并记录库的 OpenViking APIKey 与 user 身份 → 飞书 CLI。
- 不在对话中展示、复述、记录 APIKey、Token、Cookie 等敏感凭据明文；配置凭据
  优先走环境变量或配置文件，不要求用户把 Key 粘贴进聊天。
- **建库是计费动作**，且每账号最多 20 个库：创建前必须向用户确认。
- 默认创建**个人版**库，除非用户明确要求团队版。
- 示例中避免真实客户名，统一用"某客户 / 某商机 / 某项目"等泛化表达。

## Step 1：检查 AgentPlan APIKey

1. 检查环境（如 `AGENTPLAN_API_KEY` 环境变量、已安全记录的凭据、会话安全上下文）
   中是否已有 AgentPlan APIKey。只判断"是否存在/是否可用"，不要输出明文。
2. 有 Key 时，用一次**只读**控制面调用验证可用性（`list_collections` 或
   `ov-cp list`）——只读操作不消耗 AgentPlan 额度。
3. 没有 Key 时，引导用户去方舟控制台购买 AgentPlan 并新建 APIKey：
   https://console.volcengine.com/ark/region:cn-beijing/subscription/agent-plan?projectName=default
   然后暂停建库及之后的流程，等用户配置好再继续。
4. 注意：即使有了 AgentPlan APIKey，**建库还要求账号已开通 AgentPlan 抵扣**。
   如果后面 create 返回 `ProductUnordered`（"尚未在 OpenViking 开通 AgentPlan
   抵扣"），说明用户买了 Key 但没开通抵扣，引导回同一控制台页面完成开通，
   不要重试 create。

## Step 2：确认控制面能力（必要时征求安装同意）

创建 OV 库需要**控制面** MCP 或 `ov-cp` CLI。先检测当前环境是否已具备
（MCP 工具列表里有没有 `create_collection`，或 `ov-cp --help` 能否执行）。

如果不具备，**不要默默安装**——明确询问用户，说清两种方式让用户选。
包发布在 PyPI（`mcp-server-openviking-controlplane`，MCP server 和 `ov-cp`
CLI 在同一个包里），境内网络给 uv 配 PyPI 镜像即可，不依赖 GitHub：

1. **一次性使用（推荐先试这个）**：不落任何持久配置，用 `uvx` 临时拉起 CLI：

   ```bash
   # 境内网络可加：export UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
   AGENTPLAN_API_KEY=<已配置的key> uvx --from mcp-server-openviking-controlplane ov-cp list
   ```

2. **安装为 MCP（适合长期使用）**：向用户展示将写入 `.mcp.json` 的内容，
   经确认后再写：

   ```json
   {
     "mcpServers": {
       "openviking-controlplane": {
         "command": "uvx",
         "args": ["mcp-server-openviking-controlplane"],
         "env": { "AGENTPLAN_API_KEY": "${AGENTPLAN_API_KEY}" }
       }
     }
   }
   ```

需要征求同意的原因：安装会改动用户的 MCP 配置、引入外部代码，且随后的建库
动作计费。用户拒绝安装时，说明没有控制面能力就无法自动建库，但仍可引导用户
在控制台手工建库后回来继续（见 Step 3 的控制台路径）。

> 备选安装源：`uvx --from 'git+https://github.com/volcengine/mcp-server#subdirectory=server/mcp_server_openviking_controlplane' ov-cp`
> （需要能访问 GitHub）。两条路都失败时，走控制台手工建库路径。

## Step 3：创建个人版 OpenViking 库

1. 询问库名（不要自行编造）。库名建议用**英文/下划线**（如
   `sales_opportunity_kb`），中文名可能不被接受；可另记一个中文别名用于展示。
2. 建库前向用户确认：这是计费动作，且每账号最多 20 个库。
3. 用户确认后调用 `create_collection`（或 `ov-cp create --name <库名>`）。
   个人版走默认参数即可，模型配置会自动回落到 AgentPlan。
4. 创建返回 `ResourceID`（形如 `ov-xxxxxxxx`），**此时库还没就绪，返回结果里
   也没有 OpenViking APIKey**——这是正常的，进入 Step 4。

## Step 4：等库就绪，获取并记录 OpenViking APIKey 与 user 身份

库创建后处于 `INIT` 状态，需要轮询到 `READY` 才能取凭据（INIT 阶段取
api-key 会超时，不是故障，等一会重试即可）：

1. 用 `get_collection`（或 `ov-cp get <ResourceID>`）轮询 `Status`，
   直到 `READY`（通常几分钟内）。
2. 调 `get_collection_api_key`（或 `ov-cp api-key <ResourceID>`），返回
   `{UserID, Role, ApiKey}` —— 这里的 `ApiKey` 就是该库的
   **OpenViking APIKey**，`UserID` 就是 user 身份。
3. 安全记录：库名、`ResourceID`、`UserID`、OpenViking APIKey。回复中只说明
   "已安全记录"，不展示明文。
4. 之后数据面 MCP 读写该库（上传、查询、沉淀）一律用这把 Key。

**控制台兜底路径**（MCP 不可用、api-key 调用被拦、或用户已有存量库时同样适用）：
引导用户打开火山引擎 **OpenViking Service 控制台** → 在**左侧选择对应的库** →
进入**「鉴权管理」** → 点击**「显示鉴权凭证」**，即可拿到该库的 OpenViking
APIKey。让用户把 Key 配置到数据面工具的环境变量/配置中，不要粘贴进聊天。

## Step 5：检查并安装飞书 CLI

1. 检查飞书 CLI 是否可用（命令存在、已登录、能访问飞书文档）。
2. 未安装时，按官方安装文档自动安装（安装前说明动作和来源，只用官方渠道）：
   https://open.feishu.cn/document/no_class/mcp-archive/feishu-cli-installation-guide.md
3. 安装后继续检查登录/授权状态；需要扫码或授权时提示用户完成。
4. 安装失败时给出原因、已执行步骤和重试建议。飞书 CLI 不可用时不执行文档
   上传，但可以先讲清后续流程。

## Step 6：环境就绪后的引导

以下条件全部满足后，进入业务引导：AgentPlan APIKey 可用；OV 库已创建或已
选择且状态 READY；OpenViking APIKey 与 user 身份已记录；飞书 CLI 可用。

使用以下结构回复：

```markdown
销售商机商单 Builder 已就绪。

已完成：
1. AgentPlan APIKey 可用（用于建库、联网、数据集查询）。
2. OpenViking 个人库「[库名]」已就绪（READY）。
3. 该库的 OpenViking APIKey 与 user 身份已安全记录（用于读写库数据）。
4. 飞书 CLI 已就绪。

你可以直接这样说：
1. **上传资料**：把这份飞书文档上传到"[库名]"。
2. **查询商机**：总结某商机当前进展、风险点和下一步动作。
3. **生成跟进计划**：基于已上传材料，生成下一次客户沟通提纲。
4. **沉淀记忆**：每天 19:00 把今天销售相关 session 摘要上传到"[库名]"。

也可以先发我第一批飞书文档链接，我帮你上传。
```

## 场景 A：数据上传

把飞书文档、会议纪要、方案材料、报价说明、商单复盘等上传到 OV 库。

1. 让用户提供飞书文档链接、标题、文件夹或资料范围。
2. 用飞书 CLI 读取文档内容和元信息。
3. 用已记录的 OpenViking APIKey 调数据面 MCP 上传到该库。
4. 完成后返回：已上传数量、成功/失败列表（失败给原因和重试建议）、
   可立即尝试的查询问题示例。

## 场景 B：销售商机 / 商单查询

1. 识别查询对象：商机、商单、客户、行业、阶段、负责人、时间范围。
2. 用 OpenViking APIKey 调数据面 MCP 在库中检索。
3. 优先输出可执行结论，用固定结构：

```markdown
## 结论
[一句话概括]
## 当前进展
## 关键风险
## 下一步建议
## 需要补充的资料
```

4. 信息不足时明确列出缺失数据，建议用户上传哪些资料。

推荐 query 示例：

- "总结某商机当前进展、关键决策人、阻塞点和下一步动作。"
- "这个商单目前最大的成交风险是什么？"
- "从历史会议纪要里提取客户最关心的 3 个问题。"
- "帮我生成下一次客户跟进的沟通提纲。"
- "列出本周需要跟进的商机和建议动作。"

## 场景 C：session 记忆沉淀

把 Mira 中的销售跟进会话、复盘内容定时上传到 OV，形成可查询的个人销售记忆。

1. 询问同步范围（当前会话 / 指定项目 / 最近 N 天 / 关键词）和频率
   （每天、每周、会话结束后、指定时间）。
2. 用定时任务能力创建任务；执行时用 OpenViking APIKey 上传摘要或全文。
3. 上传内容带元信息：时间、主题、关联商机、来源会话、摘要、待办。
4. 不上传无关闲聊、敏感凭据或用户明确排除的内容。
5. 告知用户定时任务可暂停、可删除、可改频率。

建议上传结构：

```markdown
# 销售跟进会话沉淀
## 基本信息（时间 / 关联商机 / 来源：Mira session）
## 摘要
## 客户关注点
## 风险与阻塞
## 下一步待办
```

## 异常速查

| 现象 | 含义 | 处理 |
|---|---|---|
| 无 AgentPlan APIKey | 未购买/未配置 | 给控制台链接，暂停建库 |
| create 返回 `ProductUnordered` | 未开通 AgentPlan 抵扣 | 引导控制台开通抵扣，不要重试 |
| create 返回超限 | 已达 20 库上限 | 让用户删除闲置库或复用现有库 |
| api-key 调用超时 | 库还在 INIT | 轮询 `get_collection` 到 READY 再取 |
| 控制面 MCP / `ov-cp` 不可用 | 缺控制面能力 | 走 Step 2 征求安装同意，或控制台手工建库 |
| 拿不到 OpenViking APIKey | — | 控制台「鉴权管理 → 显示鉴权凭证」兜底 |
| 数据面读写鉴权失败 | 可能 Key 用混了 | 确认用的是该库的 OpenViking APIKey |
| 飞书 CLI 不可用 | — | 给官方安装链接，等待安装/授权 |
| 查询无结果 | 库里没有相关内容 | 说明未找到，建议上传哪些文档 |

## 安全与合规

- 不要求用户在聊天中粘贴任何 APIKey；必须配置时指导用环境变量、凭据管理
  或控制台。
- 不输出敏感凭据、客户隐私、合同金额等明文，除非用户明确要求且有授权。
- 上传资料前确认用户有权处理相关飞书文档。
- 改动用户本地配置（安装 MCP、写配置文件）前先征求同意；如目标文件已存在,
  先备份再合并，不做破坏性覆盖。
