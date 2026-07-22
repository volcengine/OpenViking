# `ov compile` 技术设计

| 项目 | 信息 |
| --- | --- |
| 状态 | 待实现 |
| 目标版本 | v1 |
| 更新日期 | 2026-07-20 |

## 1. 概述

`ov compile` 使用指定 Skill 整理 OpenViking 中的材料，并在目标目录生成或更新 Wiki 页面。

命令由 VikingBot 执行。`ov` CLI 通过 OpenViking 的 Bot 代理调用 VikingBot，VikingBot 运行 AgentLoop，并使用当前用户身份读取和写入 OpenViking 数据。

```text
ov compile
  -> OpenViking /bot/v1/compile
  -> VikingBot Compile AgentLoop
  -> OpenViking content APIs
```

v1 的核心目标：

- 支持一个或多个来源目录；
- 加载用户指定的 OV Skill；
- 根据任务描述生成一个或多个 Wiki 页面；
- 增量更新已有 Wiki；
- 通过异步任务返回进度和结果。

## 2. 用户接口

### 2.1 命令格式

```bash
ov compile \
  --from viking://resources/周报 \
  --to viking://resources/团队知识库 \
  --reason "按月整理团队的成本优化进展" \
  --skill viking://agent/skills/monthly_wiki \
  --wait
```

| 参数 | 规则 |
| --- | --- |
| `--from` | 必填，可重复，也可使用逗号分隔多个目录 |
| `--to` | 必填，目标 Wiki 目录 |
| `--skill` | 必填，Skill 目录或 `SKILL.md` 的 Viking URI |
| `--reason` | 可选，本次整理任务的描述 |
| `--wait` | 可选，等待任务完成 |
| `--timeout` | 可选，仅与 `--wait` 一起使用；只限制 CLI 等待时间，不取消任务 |

参数在 OpenViking 用户身份下 canonicalize 后满足以下约束：

- `from` 必须是一个或多个可读目录；重复项去重，空项报错；
- `to` 必须是可写的 resource 或 memory 目录，不能是 namespace 根、文件、Skill 目录或 OpenViking 派生目录；
- `skill` 必须解析为 Skill root，目录 URI 和其 `SKILL.md` URI 视为同一个 Skill；
- `from`、`to` 和 `skill` 的权限最终仍由 OpenViking Server 校验，CLI 不根据 URI 文本推断权限。

`--reason` 为空时，VikingBot 使用以下默认任务描述：

```text
Follow the loaded Skill's instructions to transform the provided source materials into the outputs required by the Skill.
```

### 2.2 返回结果

未指定 `--wait` 时，CLI 在任务创建后返回：

```text
task_id: cmp_01...
status: accepted
to: viking://resources/团队知识库
```

指定 `--wait` 时，CLI 轮询任务并返回最终结果：

```text
to: viking://resources/团队知识库
created: 1
updated: 2
unchanged: 3
page_count: 6
link_count: 8
```

完整 URI 列表通过全局 JSON 输出返回。

`created`、`updated` 和 `unchanged` 只统计 Agent 本次提交的页面；未被草稿触达的目标页面不计入 `unchanged`。`page_count` 等于三者之和，`link_count` 只统计最终正文中实际渲染出的 bundle 内 WikiLink。

`--wait` 使用单调时钟计算整体等待 deadline，并以有上限的 polling interval 查询任务；CLI timeout 或 Ctrl-C 只结束本地等待，不向 Bot 发送取消请求。

## 3. 架构

```text
┌──────────────┐
│ ov CLI       │
└──────┬───────┘
       │ POST /bot/v1/compile
       ▼
┌────────────────────────────┐
│ OpenViking Bot Proxy       │
│ auth + identity forwarding │
└──────────────┬─────────────┘
               ▼
┌────────────────────────────┐
│ VikingBot                  │
│                            │
│ Compile Task               │
│   ├─ Skill Loader          │
│   ├─ Context Tools         │
│   ├─ AgentLoop             │
│   ├─ Wiki Renderer         │
│   └─ OpenViking Writer     │
└──────────────┬─────────────┘
               │ read / search / batch-write
               ▼
┌────────────────────────────┐
│ OpenViking Data APIs       │
└────────────────────────────┘
```

职责划分：

| 模块 | 职责 |
| --- | --- |
| `crates/ov_cli` | 参数解析、HTTP 调用、任务轮询和结果展示 |
| `openviking/server/routers/bot.py` | 认证请求并代理到 VikingBot |
| `bot/vikingbot/compile` | Compile 任务、Skill、AgentLoop、渲染和写入编排 |
| OpenViking content service | 数据权限、内容读写和索引刷新 |

OpenViking Server 必须启用 Bot 服务。未启用时，命令返回与 `ov chat` 一致的 503 错误。

### 3.1 现有能力复用

Compile 只增加任务编排和领域规则，基础能力使用现有实现：

| 步骤 | 复用实现 | Compile 适配 |
| --- | --- | --- |
| CLI | `CliContext`、`HttpClient`、全局认证、`OutputFormat`、`output_success()` | compile request、状态轮询和 human formatter |
| Bot proxy | `get_bot_url()`、`_create_bot_proxy_client()`、`_attach_openviking_connection()` | create/status 路由 |
| Gateway 认证 | `OpenAPIChannel` 的 Gateway Token dependency、`OpenVikingConnection` 和 principal scope | compile request model 和 task owner 绑定 |
| URI 与权限 | `fs/attrs` 返回的 canonical URI、`validate_viking_uri()`、`canonicalize_uri()`、`context_type_for_uri()`、VikingFS access check | 用户上下文中的目录约束和 target containment |
| Skill | OpenViking Skills API、`SkillLoader.parse()`、VikingBot `SkillsLoader`、`SandboxManager` | OV bundle 快照和 task-local materialization |
| Agent | `AgentLoop._run_agent_loop()`、`ToolRegistry`、`register_default_tools()` | structured wrapper、scope guard 和 `submit_wiki_bundle` |
| 内容读取 | `openviking_list/search/grep/glob/multi_read` | 限定允许的 URI roots；不增加同义读取工具 |
| Link 与 metadata | `WikiLink`、`StoredLink`、`LinkRenderer`；Memory 目标额外复用 `MemoryFileUtils`、`next_memory_version()` 和 resource refs helper | OKF path、citation 和严格校验 |
| 写入与刷新 | `ContentWriteCoordinator` 的校验/refresh helper、`LockManager`、`VikingFS.write_file(..., lock_handle=...)`、`RequestWaitTracker` | batch precondition 和多文件编排 |

新增能力保持在以下边界内：

- Bot 侧 Compile request/task/result 和最小 task store；
- `submit_wiki_bundle` 工具及其 schema；
- Compile 特有的 OKF/path/citation 规则；
- `/bot/v1/compile` API family 和 `/api/v1/content/batch-write` 数据接口。

## 4. Compile API

### 4.1 创建任务

```http
POST /bot/v1/compile
```

```json
{
  "from": ["viking://resources/周报"],
  "to": "viking://resources/团队知识库",
  "reason": "按月整理团队的成本优化进展",
  "skill": "viking://agent/skills/monthly_wiki"
}
```

成功时返回 HTTP 202：

```json
{
  "task_id": "cmp_01...",
  "status": "accepted",
  "to": "viking://resources/团队知识库"
}
```

VikingBot 负责规范化参数并计算实际任务描述：

```python
effective_reason = (request.reason or "").strip() or DEFAULT_COMPILE_REASON
```

### 4.2 查询任务

```http
GET /bot/v1/compile/{task_id}
```

```json
{
  "task_id": "cmp_01...",
  "status": "running",
  "stage": "agent",
  "created_at": "2026-07-20T10:00:00Z",
  "updated_at": "2026-07-20T10:01:12Z"
}
```

任务状态：

| status | stage |
| --- | --- |
| `accepted` | `queued` |
| `running` | `loading_skill`、`collecting_context`、`agent`、`rendering` |
| `committing` | `writing`、`refreshing` |
| `completed` | `completed` |
| `failed` | 失败时所在阶段 |

完成结果：

```json
{
  "task_id": "cmp_01...",
  "status": "completed",
  "result": {
    "from": ["viking://resources/周报"],
    "to": "viking://resources/团队知识库",
    "skill": "viking://agent/skills/monthly_wiki",
    "okf_version": "0.1",
    "created": ["viking://resources/团队知识库/成本优化月度进展.md"],
    "updated": [],
    "unchanged": [],
    "page_count": 1,
    "link_count": 0,
    "warnings": []
  }
}
```

任务只能由创建它的用户查询。

失败结果使用同一查询接口返回稳定结构：

```json
{
  "task_id": "cmp_01...",
  "status": "failed",
  "stage": "writing",
  "error": {
    "code": "WRITE_CONFLICT",
    "message": "Target Wiki changed while the compile task was running."
  }
}
```

创建请求继续通过 body 中的 `openviking_connection` 传递当前用户身份。查询请求是 GET，没有 body；OpenViking proxy 转发原认证凭证，并从已认证的 `RequestContext` 设置 canonical `X-OpenViking-Account/User` header。VikingBot 先做现有 Gateway Token/loopback 校验，再通过 `_resolve_request_principal()` 向 OpenViking 验证凭证并计算 principal scope。无权查询与 task 不存在统一返回 `NOT_FOUND`，避免泄露其他用户的 task ID。

## 5. 执行流程

VikingBot 创建异步任务后依次执行：

1. 计算 `effective_reason`，并对 `from`、`to` 和 `skill` 做 URI 语法校验。
2. 通过 OpenViking 现有 `fs/attrs` 取得来源和目标的 canonical URI，再用 stat/list/read 路径验证形状与权限；Skill API 直接返回 canonical Skill root。VikingBot 后续只使用这些响应中的 canonical URI。
3. 通过 Skills API 取得 Skill root、定义和文件清单，通过现有 content read/download 路径读取辅助文件，在 task workspace 中物化快照，并交给 `SkillsLoader` 加载。
4. 为每个来源建立 `source_id + directory_uri + overview` 描述，并使用现有 list/tree 能力建立目标 Wiki 的有界轻量 catalog。
5. 从现有 `ToolRegistry` 构建 request-local 工具集，用显式 Compile Prompt 和 selected Skill 正文运行 structured AgentLoop；不加载普通 chat history、自动 memory recall 或其他 workspace Skill。
6. 接收 Agent 提交的结构化 `WikiBundleDraft`。
7. 对草稿中的每个 `update_uri` 读取一次最新 raw content，生成 base hash；新页面不需要预读全部目标正文。
8. 校验并渲染最终 Wiki 文件，区分 created、updated 和 unchanged。
9. 有 write operation 时通过 batch-write 一次提交并等待索引刷新；空 bundle 或全部 unchanged 时跳过写入接口。
10. 保存任务结果并清理 task workspace。

其中 AgentLoop 是唯一的内容生成阶段。后续校验、路径生成和写入都是确定性操作。

## 6. Skill 与上下文

### 6.1 Skill 加载

`--skill` 支持 Skill 目录或目录内的 `SKILL.md`。

VikingBot 从 canonical Skill URI 拆出 `skill_name` 和 `target_uri`，调用现有 Skills API 取得 Skill root、`SKILL.md` 和文件列表，再通过同一用户连接调用现有 content read/download 路径读取辅助文件；确定性加载阶段不调用 Agent tool，也不解析 `openviking_multi_read` 的展示文本。`SKILL.md` 使用现有 `openviking.core.skill_loader.SkillLoader.parse()` 校验并取得 `allowed_tools`；该 parser 增加 `allowed_tools_declared` 布尔值，以保留“未声明”和“显式空数组”的区别，Compile 不为此再解析一遍 YAML。快照物化到 task-local workspace 后，使用现有 `vikingbot.agent.skills.SkillsLoader` 加载正文和 VikingBot metadata。requirements 使用 `SkillsLoader` 解析出的 `requires.bins/env`，但在实际 task sandbox 中做存在性检查，避免使用 Bot host 环境误判。

该层只负责远程 bundle 的快照和物化，不实现新的 frontmatter parser、Skill 目录规范或 requirements 协议。OpenViking 派生文件和 Skill source metadata 不进入快照；加载过程限制文件数量、单文件大小和总大小，并拒绝逃逸 Skill root 的相对路径。task workspace 只包含本次选择的 Skill，selected Skill 正文直接加入 structured system prompt。任务结束后先调用 `SandboxManager.cleanup_session()` 停止 backend，再删除 compile 专属 workspace；现有 `cleanup_session()` 本身不会删除 direct-backend 目录，不能把它当成文件清理。

Skill 用于描述整理方法，例如：

- 应关注哪些信息；
- 页面如何分层；
- 使用什么表达风格；
- 何时生成索引页或专题页。

### 6.2 来源上下文

VikingBot 按 canonical `from` 顺序为每个来源分配稳定的 request-local ID：

```text
source_id, directory_uri, overview
```

`source_id` 只标识用户传入的来源目录，例如 `src_1`；它不是文件读取追踪 ID。Agent 首先获得这些来源描述和有界轻量目录信息，再通过 VikingBot 已有 OpenViking 工具按需读取：

- `openviking_list`：浏览来源目录；
- `openviking_search`：语义检索；
- `openviking_grep` / `openviking_glob`：按内容或路径查找；
- `openviking_multi_read`：读取具体内容和 overview。

Compile 不注册另一组 source tools。它在现有工具执行前增加 request-local URI scope guard：所有 URI 参数必须位于 `from`、`to` 或 Skill root 内；`openviking_search/list/grep/glob` 不能省略 scope 后退化为全库查询；`multi_read` 的 URI 数量、递归 list 的节点数、单次结果和任务累计工具结果字节数受 Compile 上限约束。原工具没有上限的地方由这个 guard 补齐，但实际读取和权限判断仍由原工具完成。

### 6.3 目标上下文

运行 Agent 前，VikingBot 使用现有 list/tree/read API 建立目标 Wiki catalog：

```text
page_id, uri, title, type, summary
```

catalog 只保存目录项和 L0/L1 可得的轻量信息，不为了计算 hash 或 outgoing links 预读全部目标正文。Agent 可以按需读取已有页面，用于判断创建、更新或复用；只有最终草稿选中的 `update_uri` 会在渲染前读取 raw content 并计算 precondition hash。未被本次结果引用的已有页面保持不变。

### 6.4 工具集合

`request_tools` 从 `register_default_tools()` 创建的 task-local registry 中筛选：

```text
available_tools = 已注册工具 ∩ Bot 运行策略 ∩ Compile 允许能力
core_read_tools = available_tools ∩ {read_file, openviking_list, openviking_multi_read}
skill_tools = available_tools ∩ normalize(skill.allowed-tools)  # 声明时
skill_tools = available_tools                                  # 未声明时
request_tools = (core_read_tools ∪ skill_tools) - Compile 禁用工具 + submit_wiki_bundle
```

`read_file`、`openviking_list` 和 `openviking_multi_read` 是 Compile 核心读取工具，不受 Skill `allowed-tools` 筛选，但仍受 Bot policy、task workspace、OpenViking 用户权限和 Compile URI scope 限制。`allowed-tools` 必须是字符串数组；显式 `allowed-tools: []` 表示除这三个核心读取工具和 `submit_wiki_bundle` 外不提供其他工具。名称先精确匹配 VikingBot registry name，再支持固定别名 `Read -> read_file`、`Write -> write_file`、`Edit -> edit_file`、`List/ListDir -> list_dir`、`Bash/Shell -> exec`、`WebFetch -> web_fetch`、`WebSearch -> web_search`；未知名称在 Agent 启动前返回 `SKILL_CAPABILITY_UNAVAILABLE`，不做模糊映射。OpenViking 工具使用真实的 `openviking_*` 名称，MCP 使用完整注册名。

Compile 无条件排除 `message`、`cron`、`spawn`、OpenViking 写入/提交工具以及其他会改变 Bot 会话或任务调度状态的工具。除核心 `read_file` 外的本地文件工具、shell、Web、image 和 MCP 等能力只有同时符合本节的 `allowed-tools` 规则与 Bot policy 才可用。Skill 声明 MCP 工具，或未声明 `allowed-tools` 且 Bot policy 允许 MCP 时，request-local AgentLoop 先复用 `_connect_mcp()` 完成注册，再筛选 registry；任务结束统一调用 `close_mcp()`。

本地文件工具受 task workspace 路径检查约束。shell、Web 和 MCP 仍可能产生外部副作用，只能在 Skill 声明和 Bot policy 都允许时注册；现有 `direct` sandbox 只提供 task cwd，不是 OS 级隔离。远程多用户部署若允许 shell，必须使用隔离 backend 或由管理员明确接受 direct backend 的主机权限风险。

## 7. AgentLoop 输出协议

Compile 不实现第二套 loop。VikingBot 在现有 `AgentLoop._run_agent_loop()` 上提供薄的 `run_structured_task()` 入口，并复用已有参数：

```python
await agent_loop.run_structured_task(
    system_prompt=compile_system_prompt,
    user_prompt=compile_user_prompt,
    session_key=SessionKey(type="compile", channel_id=task_id, chat_id=task_id),
    tool_registry=request_tools,
    openviking_tool_names=openviking_read_tool_names,
    stop_tool_names=["submit_wiki_bundle"],
    openviking_connection=connection,
)
```

BotCompileService 使用当前 provider/config、`workspace=task_workspace` 和 task-local `SandboxManager` 创建 request-local `AgentLoop`。`run_structured_task()` 用显式的 system/user prompt 建立 messages 后委托给 `_run_agent_loop()`；后者增加可选 `tool_registry` 和 `openviking_tool_names` 参数，并以选定 registry 同时生成 definitions 和执行工具。只有名称属于 `openviking_tool_names` 的现有 OV adapter 才在 `ToolContext`/post-call hook 中收到用户 connection；其他 file/shell/Web/MCP/custom tool 收到 `None`。普通 chat 未传这些参数时仍使用 `self.tools` 和现有 connection 行为。

该入口不使用普通 chat history、自动 memory/experience recall 或普通最终回答。只有 `submit_wiki_bundle` 成功执行并保存合法 bundle 后才能结束；参数校验或领域校验返回 `Error:` 时继续同一 loop 修复。只有自然语言而没有 submit 时，wrapper 追加提交提醒后继续；达到 iteration limit 时直接返回 `AGENT_OUTPUT_INVALID`，不执行现有聊天路径的“禁用工具后再回答一次”。模型调用、工具执行和 token usage 仍沿用现有实现。

现有 `_run_agent_loop()` 的 stop 判定需要从“出现 stop tool name”改成“该 stop tool 的结果通过 `_is_tool_result_success()`”；这是 structured task 正确重试的必要条件，默认聊天未传 `stop_tool_names`，行为不变。

`request_tools` 仍使用现有 `ToolRegistry` 中的工具实例；OpenViking 权限不在 Bot 中模拟，实际调用继续由 Server 校验。`submit_wiki_bundle` 最后注册且不能被同名工具覆盖。Skill 需要 shell、文件或其他工具时，继续在 task-local `SandboxManager` 中执行。

Agent 必须通过 `submit_wiki_bundle` 提交最终结果。

核心结构：

```python
class WikiPageDraft(BaseModel):
    page_id: int
    title: str
    page_type: str
    summary: str
    body_markdown: str
    source_ids: list[str]
    tags: list[str] = Field(default_factory=list)
    path_hint: str | None = None
    update_uri: str | None = None

class WikiBundleDraft(BaseModel):
    pages: list[WikiPageDraft]
    links: list[WikiLink] = Field(default_factory=list)
```

`WikiLink` 直接复用 `openviking.session.memory.dataclass.WikiLink` 做运行时校验，使用其 `f/t/link_type/weight/match_text/description` 字段，不定义 compile 专属 link model。`submit_wiki_bundle` 的 tool schema 将 `match_text` 描述覆盖为“必须出现在来源草稿正文中的锚点”，避免沿用 Memory 模型中“original conversation”的提示语义。

约束：

- `pages=[]` 表示没有足够依据生成可靠页面，此时 `links` 必须为空，任务成功但返回 warning；
- `page_id` 在 bundle 内唯一；
- `update_uri` 必须来自目标 catalog；
- update 保持原 URI，不能通过 `path_hint` rename 或 move；create 的 `path_hint` 只能是 `to` 下的相对 Markdown 路径；
- create 的最终 canonical path 不能与 catalog 中的已有文件或本 bundle 的其他页面冲突；并发创建同一路径仍由 batch precondition 拦截；
- link 的 `f/t` 必须非空、非 self-link，并引用 bundle 中的页面；
- `pages` 非空时，每个页面至少引用一个 `source_id`，且必须来自本次请求的来源描述；
- Agent 不提供最终文件 URI，也不能直接写入 OpenViking。

Pydantic model 使用 `extra="forbid"`；字段校验和 CompileLimits 都在 `submit_wiki_bundle` 内执行。校验失败时，工具将错误返回给 Agent 修复。达到迭代上限仍未提交合法结果时，任务失败。

页面数量由 reason、Skill 和材料决定。高层总结可以只生成一个页面，`link_count=0` 是合法结果。

## 8. Wiki 渲染与写入

VikingBot renderer 将 `WikiBundleDraft` 转成最终写入计划。Compile 新代码只负责 OKF、目标路径和 citation 规则，其余复用现有内容模型：

1. 解析已有 OKF frontmatter；Memory 目标先用 `MemoryFileUtils.read()` 分离可见文档与 hidden metadata，Resource 目标不生成 `MEMORY_FIELDS`。
2. 将现有 `ExtractLoop._resolve_links()` 中 page ID 解析、self-link 和去重的纯逻辑提取为共享 helper；Compile 使用严格校验模式。
3. 使用 `LinkRenderer` 已有的 anchor 查找、竞争处理和 escaping 生成相对 WikiLink，并补充 canonical target-root 相对路径与 Markdown protected span 两个纯 helper。
4. 确定性生成 OKF v0.1 concept frontmatter、目标路径和 citation section，Agent 不直接生成 YAML。
5. Memory 目标把 resolved `StoredLink` 合并到 `links/backlinks`、复用 resource refs helper，并用 `MemoryFileUtils` round-trip metadata；Resource 目标只存储 OKF Markdown。
6. 对比最终 raw bytes，区分 created、updated 和 unchanged，并为更新绑定渲染前读取的 `content_hash`。

### 8.1 OKF 与 metadata

v1 以 [Open Knowledge Format v0.1 Draft](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md) 为格式基线。每个 Compile 页面都是 UTF-8 Markdown concept document：YAML frontmatter 中 `type` 必填；OpenViking 额外要求 `title` 和单行 `description` 非空，`tags` 可选。

字段映射固定为：

| Draft | OKF frontmatter |
| --- | --- |
| `page_type` | `type` |
| `title` | `title` |
| `summary` | `description` |
| `tags` | `tags`，trim、去空并稳定去重；空列表不输出 |

renderer 使用 `yaml.safe_dump(allow_unicode=True, sort_keys=False)` 生成 YAML，拒绝 `body_markdown` 中的第二份 frontmatter。`title`、`page_type` 和 `summary` trim 后必须非空，`summary` 不允许换行。update 保留不与上述平台字段冲突的未知 frontmatter 字段；不自动生成 `timestamp`，避免重复执行仅因时间变化产生更新。

只有 Memory 目标使用 `MemoryFileUtils` round-trip `MEMORY_FIELDS`。create 写入 `category=page_type` 和 `version=1`；update 保留未知字段、同步 `category`，先以原 version 生成 candidate，除 version 外的最终 raw bytes 发生变化时才通过 `next_memory_version()` 推进 version。为避免 hidden links 再次命中 frontmatter，`MemoryFileUtils.write()` 增加默认保持现状的 `render_links=True` 参数，Compile 在已经渲染可见正文后以 `render_links=False` 调用。Resource 目标不写 `category`、`version` 或其他 Memory metadata。

concept 页面不写 `okf_version`；该字段按 OKF 只能出现在 bundle-root `index.md`。v1 不生成或修改 `index.md`、`log.md`，但保留目标中已有的这些文件。API/result 中的 `okf_version: "0.1"` 表示本次 renderer 的目标规范版本。

### 8.2 路径、链接与 Citations

create 的目标路径通过 `sanitize_relative_viking_path()` 和 `safe_join_viking_uri()` 约束在 canonical `to` 下；`path_hint` 为空时使用 `VikingURI.sanitize_segment(title)`，并自动追加 `.md`。点号文件、`index.md`、`log.md`、OpenViking 派生文件名和清洗后的重复路径均拒绝。update 始终使用已有 URI。

bundle link 的两端必须是本次提交的页面。`match_text` 必须实际命中来源页面的 `body_markdown`，且命中位置不能位于 YAML、代码块、inline code、已有 Markdown link 或 Citations section；renderer 只对正文做 link rendering，再拼接 frontmatter 和 Citations。它使用 target-root-aware 相对路径生成标准 Markdown link，未渲染出的 link 不计入 `link_count`。Resource 目标只保留可见链接；Memory 目标还将 resolved link/backlink 合并进 `MEMORY_FIELDS`，但 v1 不写独立 relation store。

renderer 把每页 `source_ids` 映射为用户传入的 canonical source directory URI，并在可见正文末尾合并成唯一的顶层 `# Citations`。已有 citation 先保留，再按 canonical target 去重追加本次来源；最终统一渲染为连续的 `[n] [label](target)` 列表，来源目录使用 canonical URI 的末级目录名作为 label，无法取得时回退为 `Source src_n`。代码块中的同名标题不视为 citation section。Agent 也可以在正文中引用来源范围内的具体文件 URI，这些 Markdown citation 的 label 和 target 会被保留并参与去重。`viking://` 是 OpenViking 对 citation target 的内部扩展，其他 OKF consumer 未必能够解析该 scheme。

渲染完成后，以最终 UTF-8 bytes 的 SHA-256 作为 hash。candidate 与当前 raw bytes 完全一致时归入 `unchanged` 且不提交 write operation。

写入使用通用内容接口。它是现有内容写入能力的批量入口，不实现新的存储或索引协议：

```http
POST /api/v1/content/batch-write
```

```json
{
  "root_uri": "viking://resources/团队知识库",
  "wait": true,
  "timeout": 300,
  "operations": [
    {
      "uri": "viking://resources/团队知识库/成本优化月度进展.md",
      "content": "...",
      "precondition": {"kind": "create_if_absent"}
    },
    {
      "uri": "viking://resources/团队知识库/既有页面.md",
      "content": "...",
      "precondition": {
        "kind": "replace_if_hash",
        "base_hash": "sha256:..."
      }
    }
  ]
}
```

`content` 是 renderer 生成的最终 UTF-8 存储内容，不是对已有正文执行 append/replace 的编辑指令。接口只接受 create/replace，不支持 delete；请求限制 operation 数量、单文件字节数和总字节数。

Batch write 负责：

- 要求 `root_uri` 是已存在的可写目录；canonicalize 所有 URI，拒绝空 operations、重复 URI、跨 context type 以及 root 之外的目标，并按 canonical URI 稳定排序；
- 校验用户对每个目标 URI 的写权限；
- 保证目标 URI 位于 `root_uri` 下；
- 在目标 tree lock 内读取当前 raw bytes 并检查所有 precondition；
- 若当前 hash 已等于本 operation 的最终 content hash，记为 unchanged 并加入 `refresh_uris`，再检查其余 operation；因此同一请求在响应丢失或前次写入后 refresh 失败时可以安全重试，不需要单独的 idempotency-key store；
- 完成全部底层写入后，以 `refresh_uris = desired-content matches + changed_uris` 刷新语义和向量索引；Bot 正常的全量 unchanged 重跑不会调用 batch-write，因此不会产生多余 refresh；
- resource/skill 按 refresh root 合并变更，每个 root 只提交一个包含全部变更的 `SemanticMsg`，由现有 semantic pipeline 自底向上更新 `.abstract.md` 和 `.overview.md`；
- memory 为变更文件分别更新 embedding，但每个受影响目录只调用一次 `refresh_schema_overview()`；
- 将本批次产生的 refresh 工作绑定到同一个 `RequestWaitTracker`，当 `wait=true` 时统一等待一次。

hash 定义为最终 raw UTF-8 bytes 的小写 SHA-256，API 表示为 `sha256:<hex>`。`create_if_absent` 要求文件不存在；`replace_if_hash` 要求当前 hash 等于 `base_hash`。任一非 unchanged operation 的 precondition 不满足时，在本次调用发生任何新写入前返回标准 `CONFLICT`；若同一重试请求中已有 desired-content match，释放 tree lock 后仍为这些 URI 补做 refresh。VikingBot 将该冲突映射为 task error `WRITE_CONFLICT`。

实现调用链：

```text
content.batch-write router
  -> validate_viking_uri / canonicalize_uri / existing target-shape check
  -> LockManager target tree lease
  -> read current raw bytes; classify unchanged; validate all remaining preconditions
  -> for each changed operation: VikingFS.write_file(..., lock_handle=lease.handle)
  -> collect changed_uris / refresh_uris and group them by refresh scope
  -> release target tree lease
  -> register one request in RequestWaitTracker
  -> existing ContentWriteCoordinator / MemoryUpdater refresh helpers
  -> one RequestWaitTracker waits for the batch's semantic and embedding work
```

Batch coordinator 放在现有 `openviking/storage/content_write.py` 附近，并从 `ContentWriteCoordinator` 下沉双方共同使用的 target validation、SemanticMsg 构造和 refresh helper。单文件 `write()` 与 batch 使用同一组底层实现，不复制 namespace、锁、Memory、semantic 或 embedding 逻辑。Batch coordinator 不得针对每个 operation 循环调用高层 `ContentWriteCoordinator.write()`，否则每个文件都会独立触发并等待 refresh；它必须先完成所有底层写入、释放 tree lock，再对汇总后的变更执行一次批量 refresh 编排，避免 semantic processor 与请求持有的 tree lock 相互阻塞。

Memory 现有 `refresh_schema_overview()` / `refresh_file_embedding()` 会记录 warning 后吞掉部分异常。Batch 路径需要为共享 helper 增加保持旧调用行为的 `strict=False` 默认值，并以 `strict=True` 调用；overview、semantic 或 embedding 任一登记工作失败，或 `wait=true` 得到 failed queue status 时，batch 返回失败，Compile task 不能标记 completed。

该接口不是跨文件原子存储事务：precondition conflict 不会产生本次调用的部分写入，但底层 I/O 在中途失败时可能已有少量文件可见。错误路径必须释放 tree lock，并为已成功写入的 `changed_uris` 触发一次 refresh。相同请求可依据 content hash 跳过完整落盘的文件并继续；若底层留下了不等于最终 content 的残缺文件，重试必须返回冲突，不能静默覆盖。

成功响应使用 OpenViking 标准 envelope：

```json
{
  "status": "ok",
  "result": {
    "created": ["viking://resources/团队知识库/新页面.md"],
    "updated": ["viking://resources/团队知识库/既有页面.md"],
    "unchanged": [],
    "queue_status": {}
  }
}
```

Bot 以该响应为最终提交事实，不根据请求计划假定所有文件都已写入；最终 Compile result 将 renderer 预先识别的 unchanged 与 batch 响应中的 unchanged 合并、稳定去重。

任一页面在读取后被其他请求修改时，本次写入以 `WRITE_CONFLICT` 失败，不覆盖新内容。

## 9. 身份与安全

OpenViking Bot proxy 认证 CLI 请求，并将当前用户的 OpenViking connection 转交给 VikingBot。VikingBot 使用同一身份完成所有读取和写入。

OpenViking proxy 复用 `bot.py` 现有 Bot URL、httpx client、Gateway Token、身份附加和错误映射。VikingBot compile router 复用 `OpenAPIChannel._verify_gateway_request()` 和 `OpenVikingConnection`，不定义第二套 Gateway 认证或 principal 格式。

安全要求：

- task 查询校验创建者身份；
- API key 只存在于运行中任务的内存，不写入 task store 和日志；
- Agent 的 OpenViking 读取范围只包含 `from`、`to` 和 Skill；
- OpenViking adapter 的写入和删除工具不进入 request registry；Compile 管理的 Wiki 写入只能由 batch-write 完成；
- 用户 connection 只注入 scope-guarded OpenViking read adapter，不传给 file/shell/Web/MCP/custom tools；
- Skill 声明文件、shell、Web 或 MCP 只是在请求能力，不构成授权，仍需通过 Bot policy；这些工具自身可能产生 Compile 之外的副作用，不纳入 batch-write 的一致性保证；
- Compile Prompt 明确把来源正文、catalog 和工具结果视为待整理数据，不能把其中的文本当作指令；只有用户的 reason、所选 Skill 和系统 Compile 规则构成指令层；
- file tool 只能访问 task workspace；shell 的隔离强度取决于 backend，`direct` 模式可能访问 Bot host，不能表述为安全沙箱；
- 最终 URI、写入条件和 metadata 由可信代码生成；
- 日志不记录 source 正文、Skill 正文、完整 Prompt 或凭证。

远程使用时，Bot 运行在 OpenViking Server 一侧。CLI 不在用户本机启动 Bot。

## 10. 任务存储与并发

Compile task 保存在 VikingBot 的 `bot_data_path/compile_tasks/`，包含：

```text
task_id, principal_scope, sanitized_request, status, stage, timestamps, result, error
```

Bot 当前没有通用的持久化后台任务管理器，因此这里实现一个最小 JSON task store，使用 per-task lock 和临时文件原子替换。进程内以 `asyncio.Task` 集合和 semaphore 承载 accepted task；现有 `SessionManager` 继续只管理 chat JSONL，不承载 Compile 状态。

`sanitized_request` 只包含 canonical `from/to/skill` 和 effective reason；`openviking_connection` 仅由运行中 `asyncio.Task` 持有，不进入 JSON、异常详情或日志。

运行中任务目录可以保存有大小限制的 Skill 快照、catalog 和 draft，但不能保存用户凭证。任务进入终态后删除 workspace、Skill snapshot 和 draft，只保留有界的 task/result/error JSON 供查询。

VikingBot 使用独立的 compile 并发限制，并对同一 canonical 目标目录串行执行。该锁只减少同一 Bot 进程内的浪费；跨进程或人工写入冲突仍由 batch-write 的 tree lock 和 content hash 检查解决。v1 task store 以单个 VikingBot gateway 进程为部署边界，不承诺多副本共享 task 查询。

VikingBot 启动时把 store 中所有非终态任务统一标记为 `BOT_RESTARTED`，包括处于 committing 的任务；因为 API key 不落盘，重启后不能安全恢复原任务。用户可以重新提交，batch-write 通过最终 content hash 跳过已落盘内容并继续收敛。

### 10.1 v1 资源上限

v1 先使用集中定义、可测试的 `CompileLimits`，不把常量散落在 router/tool/renderer 中：

| 项目 | 默认值 |
| --- | --- |
| source roots | 16 |
| Skill files / 单文件 / 总大小 | 128 / 8 MiB / 32 MiB |
| target catalog pages | 2000 |
| initial prompt characters | 200,000 |
| tool URI count / 单次结果 / 任务累计结果 | 32 / 1 MiB / 8 MiB |
| output pages / 最终总大小 | 64 / 4 MiB |
| concurrent Compile tasks / task runtime | 2 / 30 min |

OpenViking batch-write 自己还要设置独立的 request 上限，至少覆盖 Compile 的 64 pages / 4 MiB，但不能信任 Bot 已经做过限制。超限统一返回 `RESOURCE_EXHAUSTED`。

## 11. 错误处理

| code | 场景 |
| --- | --- |
| `INVALID_ARGUMENT` | 参数缺失或 URI 格式错误 |
| `UNAVAILABLE` | Bot 未启用或不可达；与现有 `ov chat` 一致 |
| `PERMISSION_DENIED` | 无权读取来源或写入目标 |
| `NOT_FOUND` | 来源、Skill、任务不存在，或 task 不属于当前用户 |
| `SKILL_INVALID` | Skill 结构或引用不合法 |
| `SKILL_CAPABILITY_UNAVAILABLE` | Skill 声明的 requirement 或 tool 不可用 |
| `AGENT_OUTPUT_INVALID` | Agent 未提交合法 bundle |
| `MODEL_UNAVAILABLE` | 模型服务不可用 |
| `WRITE_CONFLICT` | 目标页面在任务期间发生变化 |
| `WRITE_FAILED` | 内容写入或索引刷新失败 |
| `RESOURCE_EXHAUSTED` | Skill、catalog、工具输入或输出超过 Compile 上限 |
| `DEADLINE_EXCEEDED` | Agent、batch refresh 或 CLI 等待超时 |
| `BOT_RESTARTED` | Bot 重启中断了非终态 Compile 任务 |

同步参数和服务错误沿用 OpenViking 标准 HTTP error code。任务执行错误通过 task 的 `status=failed` 和 `error` 返回；其中 batch API 的标准 `CONFLICT` 在 Compile task 中映射为更具体的 `WRITE_CONFLICT`。

## 12. 代码改动

### CLI

- `crates/ov_cli/src/main.rs`：注册 `compile` 子命令；
- `crates/ov_cli/src/commands/compile.rs`：使用 `CliContext`/`HttpClient` 请求和轮询，使用全局 `OutputFormat`/`output_success()` 输出；
- `crates/ov_cli/src/commands/mod.rs`：导出 command；
- `crates/ov_cli/src/client.rs`：增加 compile create/status 的 typed request 方法；
- `crates/ov_cli/src/help_ui.rs`：增加命令说明和示例。

### OpenViking

- `openviking/server/routers/bot.py`：基于现有 Bot proxy helper 增加 compile 创建和查询请求；
- `openviking/server/routers/content.py`：提供 batch write API；
- `openviking/service/fs_service.py`：暴露 batch coordinator，保持 router 不直接操作 VikingFS；
- `openviking/core/skill_loader.py`：在现有 parse 结果中保留 `allowed-tools` 是否声明；
- `openviking/storage/content_write.py`：在现有 target validation、锁和 refresh helper 上增加 batch coordinator；
- `openviking/session/memory/`：仅下沉 Link、Memory 或 refresh 双方共用的小型纯 helper，为 `MemoryFileUtils.write()` 增加兼容默认值的 link-render 开关，并为 refresh 增加默认关闭的 strict 失败传播；
- `sdk/python/openviking_sdk/client.py`：为 Bot 使用的现有 async/sync HTTP client 增加 `batch_write()` 和 Skill 辅助文件 download 方法。

### VikingBot

```text
bot/vikingbot/compile/
  models.py
  router.py
  service.py
  store.py
  renderer.py
```

`service.py` 只编排现有 Skills API/loader、OpenViking tools、AgentLoop 和 batch-write client；不为这些能力增加一层同义 wrapper。只有某部分出现独立状态或被第二个调用者复用时再拆文件。

同时对现有模块做小型扩展：

- `bot/vikingbot/agent/loop.py`：为 `_run_agent_loop()` 增加可选 request registry，并提供薄的 `run_structured_task()`；
- `bot/vikingbot/channels/openapi.py`：接收 `BotCompileService` 并用现有 Gateway auth/principal resolver 注册 compile router；
- `bot/vikingbot/agent/tools/`：增加 `submit_wiki_bundle` 和 request-local URI scope guard；
- `bot/vikingbot/openviking_mount/ov_server.py`：在现有 request-scoped `VikingClient` 上薄封装 Skills/read/download/batch-write 调用；
- `bot/vikingbot/cli/commands.py`：gateway 先构造共享 provider/config 所属的 AgentLoop，再创建 `BotCompileService` 并注入 OpenAPIChannel；不增加全局 service holder。

## 13. 测试与验收

至少覆盖：

- CLI 参数展开、默认 reason、`--wait` 和 timeout；
- Bot proxy 的创建/GET 查询身份转交、未启用 Bot 的 503 和上游错误；
- Skill 复用现有 parser/loader、`allowed-tools` 缺省与显式空数组、相对引用、requirements 和路径逃逸；
- request registry 始终包含 policy/Compile 允许的三个核心读取工具，并只为其余能力加入 Skill/policy/Compile 共同允许的现有工具和 `submit_wiki_bundle`；禁用 message/cron/spawn/OV write，MCP 在筛选前按需连接且任务后关闭，用户 connection 只进入 OV read adapter；
- Agent structured wrapper 复用原 loop；失败 submit 不停止、plain text 会修复、iteration limit 不额外生成普通回答，普通 chat 行为不回归；
- OpenViking 工具的 URI scope、缺省全库参数和数量/单次/累计输出上限，并确认没有注册第二组 source tools；
- 非法 bundle 的 loop 内修复、空 bundle no-op 和最终失败；
- 单页面零 link、多页面互链和已有页面更新；
- OKF frontmatter、保留未知字段、Resource/Memory 格式差异、protected anchor、路径 containment、citation merge、WikiLink、Memory version 和 resource refs；
- batch-write 复用现有锁/write/refresh helper，覆盖 canonical URI/重复 operation、权限、content hash conflict、响应丢失/refresh 失败/部分写入后的安全重试，并验证释放 tree lock 后才 refresh；
- 多文件 resource 每个 refresh root 只产生一个 SemanticMsg，memory 每个目录只刷新一次 overview，strict refresh 失败不会返回成功；
- task owner 隔离、同目标并发、终态 workspace 清理和 Bot 重启时所有非终态任务失败。

验收命令：

```bash
ov compile \
  --from viking://resources/周报 \
  --to viking://resources/团队知识库 \
  --reason "按月整理团队的成本优化进展" \
  --skill viking://agent/skills/monthly_wiki \
  --wait
```

验收结果：

1. VikingBot 加载指定 Skill 并运行 Compile AgentLoop。
2. 目标目录生成符合 OKF v0.1 的 Wiki 页面。
3. 重复执行只创建或更新发生变化的页面；最终 raw bytes 相同时不 write、不推进 Memory version。
4. 未触达的已有页面保持不变。
5. 多页面通过一次 batch-write 提交，并按 refresh scope 合并刷新。
6. 未启用 Bot 时命令返回与 `ov chat` 一致的明确错误。
