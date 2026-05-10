# [RFC] OpenClaw Plugin 支持通过 OpenViking 导入/查询 Resource 与 Skill

> 当前实现已调整：Agent 可见工具不再使用统一的 `ov_import(kind=...)`，而是拆为 `add_resource` 和 `add_skill`；手动 slash command 也对应拆为 `/add-resource` 和 `/add-skill`，不再保留 `/ov-import`。

## 背景

当前 `examples/openclaw-plugin` 已经承担 OpenClaw 与 OpenViking 的 context-engine 集成，包括 session context、memory recall/store/forget、archive expand，以及 local/remote OpenViking runtime 管理。

随着 OpenViking 的 resource 和 skill 能力完善，OpenClaw 侧也需要一个自然入口，让用户可以在 OpenClaw 对话或显式命令中把外部资料导入到 OpenViking：

- Resource：项目文档、代码仓库、网页、PDF、Markdown、本地目录等，落到 `viking://resources/...`
- Skill：`SKILL.md`、skill 目录、raw skill 内容或 MCP tool dict，落到 `viking://agent/skills/...`

本 RFC 讨论 OpenClaw plugin 侧的导入入口、HTTP 对接方式、安全边界和测试方案。

## 目标

1. 在 OpenClaw 中支持通过插件导入 OpenViking resource。
2. 在 OpenClaw 中支持通过插件导入 OpenViking skill。
3. 对外拆成两个明确入口：
   - resource：LLM tool `add_resource`，slash command `/add-resource`
   - skill：LLM tool `add_skill`，slash command `/add-skill`
4. 不再通过 `kind: "resource" | "skill"` 在一个入口里分流。
5. 保持 HTTP server 的本地路径安全边界：不把本地路径直接发给 OpenViking 服务端。
6. 底层仍复用 `OpenVikingClient.addResource()` / `OpenVikingClient.addSkill()`，避免在 resource 和 skill 之间复制上传逻辑。
7. 导入后提供检索入口，让用户能在 OpenClaw 内确认和消费已导入 resource 与 skill。

## 非目标

- 不改变 OpenViking 服务端 resource/skill API。
- 不新增 OpenViking parser 类型。
- 不依赖插件侧自动猜测所有输入类型。
- slash command 暂不支持复杂 raw `SKILL.md` 多行内容或 MCP dict JSON；这些能力通过 LLM tool 的 `data` 参数支持。

## 当前 OpenViking 支持的 Resource 输入

OpenViking resource 侧当前支持多类输入源：

- 远程 URL：`http://`、`https://`
- Git 仓库 URL：`git@`、`ssh://`、`git://`，以及常见 Git hosting URL
- 飞书/Lark 文档 URL
- 本地文件、本地目录、`.zip`
- raw text content

常见文件 parser 覆盖：

- 文档/文本：`.txt`、`.text`、`.md`、`.markdown`、`.mdown`、`.mkd`、`.pdf`、`.html`、`.htm`
- Office/电子书：`.docx`、`.doc`、`.pptx`、`.xlsx`、`.xls`、`.xlsm`、`.epub`
- 图片：`.png`、`.jpg`、`.jpeg`、`.gif`、`.bmp`、`.webp`、`.svg`
- 音频：`.mp3`、`.wav`、`.ogg`、`.flac`、`.aac`、`.m4a`、`.opus`
- 视频：`.mp4`、`.avi`、`.mov`、`.mkv`、`.webm`、`.flv`、`.wmv`
- 目录导入还会把常见代码、文档、配置扩展名作为 text 处理，例如 `.py`、`.js`、`.ts`、`.go`、`.rs`、`.java`、`.cpp`、`.json`、`.yaml`、`.toml`、`.csv`、`.rst`、`.proto`、`.tf`、`.vue`

补充说明：media parser 当前主要复制原始文件并提取基础元数据；后续向量化主要依赖摘要。

## 当前 OpenViking 支持的 Skill 输入

OpenViking 当前已经有独立的 skill 导入链路：

- HTTP API：`POST /api/v1/skills`
- Service 层：`ResourceService.add_skill(...)`
- Processor：`SkillProcessor.process_skill(...)`
- 存储位置：`viking://agent/skills/{skill_name}`

支持的输入形态包括：

- 本地 `SKILL.md` 文件
- 包含 `SKILL.md` 的 skill 目录
- zip 包，解压后按目录/文件处理
- raw `SKILL.md` 字符串内容
- dict 格式的 skill 数据
- MCP tool dict，会通过 `mcp_to_skill()` 转成 skill

HTTP 安全边界是：直接通过 HTTP 调 `/api/v1/skills` 时，不能传宿主本地路径；本地文件/目录需要先上传到 `/api/v1/resources/temp_upload`，再把返回的 `temp_file_id` 传给 `/api/v1/skills`。

## 设计方案

### 1. 对外拆成两个入口

Resource 和 skill 的落点、参数和服务端 API 不同，对外暴露两个入口：

- resource：LLM tool `add_resource`，slash command `/add-resource`
- skill：LLM tool `add_skill`，slash command `/add-skill`

Resource 导入：

```text
/add-resource ./README.md --to viking://resources/openviking-readme --wait
```

Skill 导入：

```text
/add-skill ./skills/install-openviking-memory --wait
```

### 2. 为什么拆成两个入口

- 工具名直接对应 OV 已有能力名：`add_resource` / `add_skill`
- schema 不再混入 `kind`、`data`、`to`、`parent` 这类互斥参数
- 用户手动命令也不再靠 `--kind skill` 切换语义
- 底层仍复用 `OpenVikingClient.addResource()` / `addSkill()`，不复制上传逻辑

换句话说：对外和对内都保持 resource / skill 分离。

### 3. LLM Tool 参数设计

新增工具：

```text
add_resource
add_skill
```

参数：

```ts
{
  // resource 或 skill path 模式共用
  source?: string; // local path, directory path, public URL, Git URL

  // skill 专用：raw SKILL.md 或 MCP tool dict
  data?: unknown;

  // resource 专用
  to?: string;
  parent?: string;
  reason?: string;
  instruction?: string;

  // 通用
  wait?: boolean;
  timeout?: number;
}
```

校验规则：

- `add_resource`：
  - 必须提供 `source`
  - `source` 可为本地路径、目录、远程 URL 或 Git URL
  - `to` 与 `parent` 互斥
  - 忽略或拒绝 `data`
- `add_skill`：
  - 必须提供 `source` 或 `data`
  - `source` 可为本地 `SKILL.md`、skill 目录或 zip
  - `data` 可为 raw `SKILL.md` 或 MCP tool dict
  - 拒绝 resource-only 参数：`to`、`parent`、`reason`、`instruction`

工具描述中应明确：只有当用户明确要求导入、添加或索引 OpenViking resource/skill 时才调用，避免模型误触发。

### 4. Resource 导入路径设置

Resource 的导入路径由 `to` 或 `parent` 控制，二者互斥：

- `to`：精确指定最终 resource URI。
- `parent`：只指定父目录，由 OpenViking 根据源文件名、目录名、URL 或仓库名生成子路径。
- 二者都不传：由 OpenViking 在 `viking://resources/...` 下按默认规则生成路径。

推荐用户在希望得到稳定引用 URI 时使用 `to`：

```text
/add-resource ./README.md --to viking://resources/openviking-readme --wait
```

结果会尽量落到：

```text
viking://resources/openviking-readme
```

如果只想把资源放到某个集合下，可使用 `parent`：

```text
/add-resource ./README.md --parent viking://resources/docs --wait
```

结果会落在类似：

```text
viking://resources/docs/README
```

### 5. Slash Command 参数设计

新增命令：

```text
/add-resource <source> [--to URI] [--parent URI] [--reason TEXT] [--instruction TEXT] [--wait] [--timeout SEC]
/add-skill <source> [--wait] [--timeout SEC]
```

导入 resource：

```text
/add-resource ./README.md --to viking://resources/openviking-readme --wait
```

导入远程 resource：

```text
/add-resource https://github.com/volcengine/OpenViking --to viking://resources/openviking-repo --reason "OpenViking source docs"
```

导入 skill：

```text
/add-skill ./skills/install-openviking-memory --wait
/add-skill ./SKILL.md --wait
```

Slash command 暂不支持 raw multi-line `SKILL.md` 或 MCP dict JSON；如果需要导入这些结构化数据，使用 LLM tool 的 `data` 参数。

目录导入的高级过滤参数暂不放进 v1 对外入口。后续可以按需增加：

```ts
strict?: boolean;
ignoreDirs?: string;
include?: string;
exclude?: string;
preserveStructure?: boolean;
```

### 6. Client 层实现

在 `OpenVikingClient` 增加底层方法：

- `uploadTempFile(filePath)`
- `zipDirectoryForUpload(dirPath)`
- `addResource(input)`
- `addSkill(input)`

`add_resource` 和 `add_skill` 分别调用底层 client 方法：

```ts
add_resource -> client.addResource(...)
add_skill -> client.addSkill(...)
```

本地文件/目录流程：

```text
local file/dir
  -> if dir: zip locally
  -> POST /api/v1/resources/temp_upload
  -> POST /api/v1/resources or /api/v1/skills with temp_file_id
```

远程 resource 流程：

```text
remote URL / Git URL
  -> POST /api/v1/resources with path
```

Skill raw data 流程：

```text
raw SKILL.md or MCP dict
  -> POST /api/v1/skills with data
```

### 7. 安全边界

保持 OpenViking HTTP server 当前安全模型：

- 插件不把本地文件系统路径直接发送给服务端。
- 本地文件必须先走 `/api/v1/resources/temp_upload`。
- 本地目录先在插件侧打 zip，再上传。
- zip 使用纯 JavaScript 实现，避免依赖系统 `zip` 命令。
- resource 的 `to` 与 `parent` 保持互斥，沿用服务端约束。

### 8. 导入后的检索入口：memory_search

只提供导入工具会让体验不闭环：用户可以把 resource 或 skill 导入 OpenViking，但在 OpenClaw 内不一定知道如何检索、读取或验证导入结果。

因此建议同时讨论一个轻量检索入口：

- LLM tool：`memory_search`
- slash command：`/memory-search`

`memory_search` 的目标不是替代 OpenViking CLI 的全部能力，而是提供最小闭环：

1. 导入 resource 或 skill。
2. 返回 resource 的 `root_uri` 或 skill 的 `uri`。
3. 用户或 LLM 可以立刻按返回 URI 检索。

#### add_resource 返回建议

`add_resource` 导入 resource 后，结果中应包含：

- `root_uri`
- `status`
- `queue_status`（如果 `wait=true`）
- warnings/errors
- 下一步检索建议

示例：

```text
Imported OpenViking resource: viking://resources/openviking-readme
Processing: completed
Try: /memory-search "OpenViking install" --uri viking://resources/openviking-readme
```

`add_skill` 导入 skill 后也应返回 skill URI，并给出 skill 检索建议：

```text
Imported OpenViking skill: viking://agent/skills/install-openviking-memory
Processing: completed
Try: /memory-search "<query>" --uri viking://agent/skills
```

#### memory_search 参数设计

v1 建议保持简洁：

```ts
{
  query: string;
  uri?: string;   // default: search resources and skills
  limit?: number; // default: plugin config or 10
}
```

示例命令：

```text
/memory-search "OpenViking install" --uri viking://resources/openviking-readme
/memory-search "API usage" --uri viking://resources
/memory-search "<query>" --uri viking://agent/skills
```

对应 LLM tool 示例：

```ts
{
  query: "OpenViking install",
  uri: "viking://resources/openviking-readme",
  limit: 5
}
```

#### memory_search 返回结果

`memory_search` 应尽量贴近 OpenViking 当前 `/api/v1/search/find` 的返回结构。OpenViking find 的 raw HTTP 返回外层是：

```ts
{
  status: "ok",
  result: {
    memories: MatchedContext[];
    resources: MatchedContext[];
    skills: MatchedContext[];
    total: number;
    query_plan?: unknown;
    provenance?: unknown; // include_provenance=true 时才可能出现
  }
}
```

其中 `MatchedContext` 当前包含：

```ts
{
  context_type: "memory" | "resource" | "skill";
  uri: string;
  level: number;
  score: number;
  category: string;
  match_reason: string;
  relations: Array<{
    uri: string;
    abstract: string;
  }>;
  abstract: string;
  overview?: string | null;
}
```

因此 `memory_search` 建议也返回两层结果：

1. 用户可读的 `content.text`
2. 保留 OpenViking 分桶结构的 `details`

有结果时的用户可见文本示例：

```text
Found 4 OpenViking results for "OpenViking install"

Resources
1. viking://resources/openviking-readme/README.md
   OpenViking installation guide and setup commands...
   score: 0.82

2. viking://resources/openviking-readme/INSTALL.md
   Plugin install flow for OpenClaw and OpenViking...
   score: 0.76

Skills
1. viking://agent/skills/install-openviking-memory
   Install and operate OpenViking memory integration...
   score: 0.69
```

对应结构化返回建议：

```ts
{
  content: [
    {
      type: "text",
      text: "Found 4 OpenViking results..."
    }
  ],
  details: {
    action: "searched",
    query: "OpenViking install",
    uri: "viking://resources/openviking-readme",
    total: 4,
    memories: [],
    resources: [
      {
        context_type: "resource",
        uri: "viking://resources/openviking-readme/README.md",
        level: 2,
        score: 0.82,
        category: "",
        match_reason: "...",
        relations: [],
        abstract: "OpenViking installation guide and setup commands...",
        overview: "..."
      }
    ],
    skills: [
      {
        context_type: "skill",
        uri: "viking://agent/skills/install-openviking-memory",
        level: 0,
        score: 0.69,
        category: "",
        match_reason: "...",
        relations: [],
        abstract: "Install and operate OpenViking memory integration...",
        overview: "..."
      }
    ]
  }
}
```

无结果时：

```text
No OpenViking resource or skill results found for "OpenViking install".
```

对应结构化返回：

```ts
{
  action: "searched",
  query: "OpenViking install",
  uri: "viking://resources/openviking-readme",
  total: 0,
  memories: [],
  resources: [],
  skills: []
}
```

v1 应检索 `resources` 和 `skills`，所以用户可见文本按分桶展示 `resources` 和 `skills`；`details` 中保留 OpenViking 原始的 `memories/resources/skills/total` 结构。由于 v1 不检索 memory，`memories` 通常为空数组。

v1 建议只返回检索结果摘要和 URI，不直接返回完整文件内容。原因：

- 避免把大文档直接塞进上下文。
- 用户或 LLM 可以基于 URI 再决定是否需要后续 `/ov-read` 或未来的 `ov_read`。
- 保持 `memory_search` 作为导入后的最小消费闭环，而不是完整文件浏览器。

#### 是否同时检索 memory/resource/skill

`memory_recall` 当前偏向长期记忆，不适合作为 resource/skill 导入后的默认消费入口。`memory_search` v1 应检索：

```ts
uris = ["viking://resources", "viking://agent/skills"]
```

后续如果希望统一上下文检索 memory/resource/skill，可扩展：

```ts
targetTypes?: Array<"memory" | "resource" | "skill">;
```

但 v1 不建议一开始加入 memory，避免和既有 `memory_recall` 职责重叠。

#### 读取与树形查看

`memory_search` 只解决“查到相关上下文”的问题。若用户需要更明确的文件浏览能力，后续可以再讨论：

- `/ov-read <uri>`
- `/ov-tree <uri>`

v1 暂不加入，避免把导入 RFC 扩展成完整 OpenViking CLI replica。

## 测试计划

### Unit tests

覆盖 `OpenVikingClient`：

- `addResource`
  - 远程 URL 直接 POST `path`
  - 本地文件先 temp upload
  - 本地目录先 zip 再 temp upload
  - `wait=true` 保留 `queue_status`
  - OpenViking error response 透传为可读错误
- `addSkill`
  - 本地 `SKILL.md` 先 temp upload
  - skill 目录先 zip 再 temp upload
  - raw `SKILL.md` 通过 `data` 直传
  - MCP tool dict 通过 `data` 直传

覆盖 `add_resource` / `add_skill`：

- 注册 tool：`add_resource` / `add_skill`
- 注册 command：`/add-resource` / `/add-skill`
- quoted args
- `--flag`
- `--key value`
- `--key=value`
- `--to` 与 `--parent` 冲突
- `/add-skill` 拒绝 `to`、`parent`、`reason`、`instruction`

覆盖 `memory_search`：

- 注册单个 tool：`memory_search`
- 注册单个 command：`/memory-search`
- 未指定 `uri` 时默认检索 `viking://resources` 和 `viking://agent/skills`
- 指定 `--uri` 时只检索该范围
- OpenViking error response 透传为可读错误
- 查询为空时返回 usage error

### Manual smoke

```text
/add-resource ./README.md --to viking://resources/openviking-readme --wait
/memory-search "OpenViking install" --uri viking://resources/openviking-readme
/add-skill ./skills/install-openviking-memory --wait
/memory-search "install OpenViking memory" --uri viking://agent/skills
```

以及自然语言触发：

```text
把 ./README.md 导入 OpenViking resource，目标是 viking://resources/openviking-readme，并等待处理完成。
```

```text
把 ./skills/install-openviking-memory 作为 OpenViking skill 导入。
```

## 待讨论问题

1. `add_resource` / `add_skill` 是否应默认 `wait=false`？
   - 当前建议默认不等待，只有用户显式要求“等待处理完成”或传 `wait=true` 时才等待。
2. `/add-skill` 遇到 resource-only 参数应直接报错，还是静默忽略？
   - 当前建议直接报错，避免用户误以为 `to/parent` 对 skill 生效。
3. 是否需要插件侧自动识别 skill？
   - 可选增强：当本地文件名为 `SKILL.md` 或目录包含 `SKILL.md` 时提示用户使用 `/add-skill` 或 agent tool `add_skill`。
   - 当前建议先使用显式 `/add-skill`，避免在 `/add-resource` 中自动猜测并改变用户意图。
4. 导入本地大目录前是否需要确认机制？
   - 当前方案先不加确认；后续可基于文件数量/zip 大小增加保护。
5. `memory_search` v1 是否默认检索 `viking://resources` 和 `viking://agent/skills`？
   - 当前建议是。memory 仍交给既有 `memory_recall`，避免职责重叠。
6. 是否需要在 v1 同时提供 `/ov-read` 或 `/ov-tree`？
   - 当前建议不加，先用 `memory_search` 完成导入后的最小消费闭环。
