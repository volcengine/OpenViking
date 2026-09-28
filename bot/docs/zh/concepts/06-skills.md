# VikingBot Skills

Skill 用 `SKILL.md` 描述一类任务的触发条件、操作步骤和配套资源。模型读取这些指令，再调用 VikingBot 已注册的工具完成任务。Skill 本身不注册工具，也不提供新的执行后端。

VikingBot 支持活动 Workspace 中的本地 Skill，以及保存在 OpenViking 中的远程 Skill。本文按当前代码说明行为；远程方案的设计背景见 [RFC #3656](https://github.com/volcengine/OpenViking/discussions/3656)。

## 本地与远程

| 项目 | 本地 Skill | OpenViking 远程 Skill |
|------|------------|----------------------|
| 存储 | `<active-workspace>/skills/<name>/` | OpenViking 返回的 canonical Skill URI |
| 发现 | 扫描工作区 Skill 目录 | 按当前用户问题调用 `find_skills`，只召回 L0 摘要 |
| 默认上下文 | 名称、描述、本地路径 | 名称、描述、`SKILL.md` URI、读取工具 |
| 读取正文 | `read_file` | `openviking_multi_read`；读取定义后激活 |
| `always` | 可每轮注入完整正文 | 不支持自动常驻激活 |
| 依赖检查 | 在 Bot 进程环境检查，缺失时隐藏摘要 | 激活后、首次普通工具调用前在实际执行沙箱检查 |
| `allowed-tools` | 本地加载器不据此限制工具 | 运行时强制限制当前 Turn 的工具集 |
| 配套资源 | 已在工作区内 | 文本按 URI 读取；工具需要本地路径时下载整个包 |
| 生命周期 | 文件持续保留，每轮重新构建上下文 | 每条用户消息独立激活；Turn 结束清理执行副本 |

两种 Skill 都受 Bot、渠道、请求的工具可见性和沙箱策略约束。关闭 OpenViking 工具不影响本地 Skill 加载。

## 使用本地 Skill

将 Skill 放到**活动 Workspace**，例如默认 `shared` 模式下的 `<workspace>/shared/skills/`。根目录解析与隔离模式见 [Workspace 与 Agent 定制](./02-agent-capabilities.md#workspace-与-agent-定制)。

```text
<active-workspace>/skills/report-summary/
├── SKILL.md
├── scripts/
│   └── summarize.py
├── references/
│   └── report-format.md
└── assets/
    └── template.md
```

只有直接子目录中存在 `SKILL.md` 的 Skill 才会被列出；本地名称来自目录名。默认每轮只注入满足依赖的 Skill 摘要，模型选择后读取 `skills/report-summary/SKILL.md`。`always: true` 的本地 Skill 会直接注入去掉 frontmatter 的正文，辅助文件仍需按需读取。

`bot.skills` 是从内置模板复制到活动 Workspace 的 Skill 名称列表，不是运行时扫描的白名单。初始化时只复制名单中的模板，并跳过已有的同名目录；自行放入活动 Workspace 的 Skill 无需加入该列表。显式按名称加载时，工作区文件优先于内置模板；正常摘要列表扫描的是工作区目录。

本地 Skill 中的工具路径以工作区为基准，例如 `python3 skills/report-summary/scripts/summarize.py`；不能假设读取 `SKILL.md` 会切换工作目录。

## 使用远程 Skill

### 准备与启用

1. 按 [OpenViking 集成](./04-openviking-integration.md#连接模式) 配置可用连接，并确保 Bot 当前身份有权读取目标 Skill。
2. 将 Skill 上传到同一 OpenViking 服务。有辅助文件时上传目录，保留整个包：

   ```bash
   ov add-skill ./skills/report-summary/
   ```

   CLI 应连接目标服务并使用具有相应权限的身份。保存返回的 URI；若返回后台 `task_id`，可用 `ov task status TASK_ID` 检查处理进度。完整导入方式见 [OpenViking Skills API](../../../../docs/zh/api/04-skills.md)。
3. 当前渠道保持 `ov_tools_enable: true`，并且 `openviking_multi_read` 已注册、未被 `disabled_tools` 禁用。
4. 向 Bot 描述任务，让它从远程摘要中选择 Skill；也可以明确要求读取某个 `SKILL.md` 的 canonical URI。

不需要额外的 Remote Skill 开关或 `load_skill` 工具。`bot.remote_skills` 只调整检索和缓存等参数，不提供 `enabled`、`default_materialize` 或 `materialize_mode`。

### 发现、激活和执行

每条需要回复的普通用户消息创建一个 `SkillRuntimeContext`，覆盖本条消息的全部模型与工具迭代：

```text
用户问题 → find_skills → 本地 + 远程摘要
  → openviking_multi_read(SKILL.md) → 校验并激活
  → 更新工具 Schema → 依赖检查 → 按需解析资源 → 执行工具
  → 保存结果和使用记录 → 关闭运行时、清理请求副本
```

同名候选优先级是本地工作区、OpenViking 用户 Skill、OpenViking 共享 Skill（`viking://agent/skills/`）。本地目录即使因缺少依赖未出现在摘要中，也会遮蔽同名远程候选。显式 URI 不走名称选择，应使用服务实际返回的 canonical URI；不要把名称当作跨用户唯一标识。

例如，对服务返回的用户 Skill URI 发起工具调用：

```json
{
  "name": "openviking_multi_read",
  "arguments": {
    "uris": ["viking://user/alice/skills/report-summary/SKILL.md"]
  }
}
```

激活会解析 frontmatter，并通过 `get_skill(include_integrity=true)` 校验正文、canonical URI、revision 和文件清单。此时不下载辅助文件。模型收到的正文附带包内相对路径与 canonical URI 的映射。激活需要完整正文；当前读取工具默认拒绝完整返回超过 512 KiB 的文件，因此应将大段参考资料拆到辅助文件，不能通过分段读取来激活定义。

如果模型在同一批中同时请求读取定义和执行其他工具，运行时先处理激活，其余调用返回 `SKILL_CONTEXT_UPDATED`，由模型在新 Schema 下重试；激活失败时返回 `SKILL_ACTIVATION_FAILED`，不执行同批普通工具。

## SKILL.md 与元数据

建议使用 YAML frontmatter，并将 VikingBot 扩展放在 `metadata.vikingbot` 下。下面的远程 Skill 示例声明了 Python 命令权限和依赖；`scripts/summarize.py`、`references/report-format.md` 必须实际存在于包中。

```markdown
---
name: report-summary
description: Summarize a report when the user requests a report review.
allowed-tools: Bash(python3 *)
tags: [reporting]
metadata:
  author: example-team
  version: "1.0"
  vikingbot:
    always: false
    requires:
      bins: [python3]
---

# Report summary

Read references/report-format.md with openviking_multi_read.
Run python3 scripts/summarize.py and summarize its output.
```

### 字段参考

| 字段 | 类型 / 默认 | 当前行为 |
|------|-------------|----------|
| `name` | 字符串；远程必填 | 远程激活时必须与 URI 中的 Skill 名称一致；本地仍以目录名标识 Skill，建议两者一致 |
| `description` | 字符串；远程必填 | 描述适用任务，供模型选择；本地未提供时摘要回退到目录名 |
| `allowed-tools` | 空格分隔字符串，也兼容字符串列表；默认未声明 | 远程工具策略，详见下一节；本地加载器不执行该策略 |
| `tags` | 字符串列表；默认 `[]` | OpenViking 保留的分类信息，不改变 Bot 工具权限或触发方式 |
| `metadata` | YAML 对象，也兼容 JSON 字符串 | 扩展元数据容器 |
| `metadata.vikingbot` | 对象 | VikingBot 识别的扩展作用域；存在时从此对象读取扩展字段 |
| `metadata.vikingbot.always` | 布尔值；默认 `false` | 仅本地：依赖满足时，每轮注入完整正文 |
| `always` | 顶层布尔值；默认 `false` | 本地兼容写法，与 scoped `always` 按“或”判断；远程运行时不读取 |
| `metadata.vikingbot.requires.bins` | 命令名列表；默认 `[]` | 所有命令都必须可用；不会自动安装 |
| `metadata.vikingbot.requires.env` | 环境变量名列表；默认 `[]` | 所有变量都必须满足环境检查；声明变量名，不在 Skill 中写值 |
| `metadata.vikingbot.emoji` | 字符串；可选 | 部分内置模板使用的说明信息，当前 Skill 摘要生成器不读取 |
| `metadata.vikingbot.os` | 字符串列表；可选 | 部分模板声明的平台信息，如 `[darwin, linux]`；当前加载器不据此过滤平台 |
| `metadata.vikingbot.install` | 对象列表；可选 | 部分模板中的安装说明，常含 `id`、`kind`、`bins`、`label`、`formula` 或 `package`；当前 Bot 不自动执行 |
| 其他 metadata，如 `author`、`version` | 自定义 | 可作说明信息，当前 Bot 不据此控制执行或缓存版本 |

frontmatter 的权限字段必须是 **`allowed-tools`**。`allowed_tools` 是 OpenViking 解析后的结构化数据字段，不能替代 `SKILL.md` 中带连字符的字段。用真正的 YAML 布尔值 `true` / `false`，不要写字符串 `"false"`。

以下两种旧 metadata 写法也受支持；新 Skill 建议采用上面的作用域形式，避免其他系统的扩展字段与 Bot 混在一起：

```yaml
metadata:
  requires:
    bins: [python3]
```

```yaml
metadata: '{"vikingbot":{"requires":{"bins":["python3"]}}}'
```

### 依赖检查的区别

本地加载器用 Bot 进程的 `PATH` 查找命令，并要求环境变量值非空。缺少依赖的 Skill 不进入摘要或 Always 内容；这属于发现过滤，不是执行沙箱中的权限检查。

远程运行时在真正执行工具的沙箱内检查命令；环境变量只检查是否已定义，空值也算存在，不读取或记录值。环境变量名必须符合 `[A-Za-z_][A-Za-z0-9_]*`。每个激活 Skill 在本 Turn 首次普通工具调用前检查一次，与是否下载文件无关；`openviking_multi_read` 作为读取通道不触发这项检查。远程也接受单个字符串形式的 `bins` / `env`，但统一使用列表可兼容本地加载器。

### 远程 allowed-tools

| 声明 | 含义 |
|------|------|
| 不写 `allowed-tools` | 不额外收窄已有工具集 |
| `allowed-tools: []` 或 `allowed-tools: ""` | 不允许普通任务工具；保留 `openviking_multi_read` 读取通道 |
| `allowed-tools: Read Bash` | 允许 `read_file` 和 `exec` |
| `allowed-tools: Bash(python3 *)` | 只允许匹配该模式的单条 `exec` 命令 |
| `allowed-tools: Bash(git:*)` | 兼容冒号写法，可匹配 `git ...` |

运行时支持的别名如下；也可以直接填写已注册的工具名，如 `openviking_search` 或 `mcp_<server>_<tool>`。

| Skill 写法 | VikingBot 工具 |
|------------|---------------|
| `Bash` / `exec` | `exec` |
| `Read` / `read_file` | `read_file` |
| `Write` / `write_file` | `write_file` |
| `Edit` / `edit_file` | `edit_file` |
| `Glob` | `openviking_glob` |
| `Grep` | `openviking_grep` |
| `WebFetch` / `web_fetch` | `web_fetch` |
| `WebSearch` / `web_search` | `web_search` |
| `spawn` | `spawn` |

多个远程 Skill 同时激活时，普通工具权限取所有已激活 Skill 的交集，再受原有 Bot/渠道/请求策略限制。限制覆盖本 Turn 后续的普通工具调用，不仅是引用该包文件的调用。Skill 无法恢复已禁用工具；`openviking_multi_read` 豁免 Skill 策略交集，但仍须在 Bot 中可用。

同一个 Skill 的多个 Bash 模式按“或”匹配，不同 Skill 之间按“且”匹配。模式匹配完整命令字符串；带参数约束时拒绝管道、重定向、命令连接、换行、反引号和 `$()` 等复合 shell 语法。无参数约束的 `Bash` 不增加这层命令匹配。其他工具暂不支持括号参数约束，例如 `Read(...)` 会被拒绝。需要执行脚本时应显式使用 `python3`、`bash` 等解释器，不依赖下载后的可执行位。

## 远程资源与本地路径

激活之后，文本说明仍用 `openviking_multi_read` 读取。`exec` 引用包内文件，或普通工具声明的输入参数需要本地文件/目录时，运行时才把 Skill 包下载到沙箱并改写参数。

| 输入示例 | 处理 |
|----------|------|
| `openviking_multi_read` 读取 `references/report-format.md` | 唯一匹配已激活包后转换为 canonical URI，远程读取 |
| `exec` 执行 `python3 scripts/summarize.py` | 匹配清单，下载整个包，改写脚本路径 |
| 已激活包内的完整 `viking://.../assets/template.md` | 按消费工具要求远程读取或转成本地路径 |
| 多个包都有 `scripts/summarize.py` | 拒绝模糊路径，要求完整 canonical URI |
| 普通工作区文件 `workspace:input/report.csv` | 去掉 `workspace:` 前缀，传给原本地文件工具或 `exec` |
| 当前沙箱的原生绝对路径、HTTP / Data URL | 原样交给消费工具 |
| 未匹配的裸相对文件路径、目录逃逸或未激活包的资源 | 拒绝；先确认来源或读取对应 `SKILL.md` |

`workspace:` 用于本地文件参数与 `exec`；它不是 `openviking_multi_read` 的工作区读取协议。输出也应保存在普通工作区，例如命令参数使用 `workspace:output/summary.md`，避免 Turn 结束时跟随临时 Skill 包被删除。

运行时保留包内目录结构，但不会把工具工作目录切到包根。脚本应根据自己的文件位置解析相邻模板和模块；模型直接使用返回的资源绑定，不应手工复制资源或把 `working_dir` 设置成远程 URI、缓存路径或物化目录。执行失败不会自动补下载并重跑原命令。

工具开发者通过 `Tool.resource_inputs` 声明本地输入参数，例如 `{"path": "local_file", "/files/*/workspace_path": "local_file"}`。支持 `local_file`、`local_directory` 和带 `*` 的 JSON Pointer 风格路径。未声明的参数不会仅因名字像路径而自动处理；输出参数不应声明成输入。此声明属于工具实现，不是 Skill metadata。

## 生命周期、缓存与配置

每条用户消息重新发现、激活远程 Skill；同一 Session 下一轮不会继承候选、权限交集或依赖检查结果。工具使用记录包含真实 `skill_uri` / `skill_uris` 和解析后的 `resolved_args`。当前代码沿用普通工具结果的 Session、事件和 Trace 记录流程，读取到的 Skill 正文也可能进入这些记录；RFC 提出的独立脱敏视图和正文不持久化不能视为当前实现保证。

文件分为两层存储：

| 层级 | 当前路径 | 生命周期 |
|------|----------|----------|
| Bot 主机缓存 | `<bot-data>/remote_skill_cache/` | 按权限域、canonical 根 URI 和服务端 revision 隔离，TTL/LRU 淘汰 |
| 工具执行副本 | `<sandbox>/.remote-skill/<request-id>/<root-uri-sha256>/<skill-name>/` | 当前 Turn 内复用，结束时清理；不会直接执行主缓存文件 |

这里的 `.remote-skill/` 是当前代码路径。缓存命中仍需当前身份成功读取、激活并复查 manifest；随后逐文件校验大小和 SHA-256，复制到请求沙箱。版本变化或文件校验失败时拒绝继续使用该快照。缓存减少重复下载，不跳过 OpenViking ACL，也不以 `metadata.version` 作为缓存版本。

以下字段位于 `ov.conf` 的 **`bot.remote_skills`** 对象中，都是部署配置，不是 frontmatter：

| 字段 | 默认值 | 作用 |
|------|--------|------|
| `discovery_limit` | `8` | 最多注入的远程候选数，范围 1–50 |
| `score_threshold` | `0.35` | 最低召回分数，范围 0–1 |
| `discovery_timeout_seconds` | `2.0` | 发现超时秒数，须大于 0 且不超过 30 |
| `max_files` | `128` | 单包物化文件数，包含 `SKILL.md` |
| `max_file_bytes` | `8388608`（8 MiB） | 单文件大小；读取激活的 `SKILL.md` 也受此限制 |
| `max_total_bytes` | `33554432`（32 MiB） | 单包物化总大小 |
| `cache_idle_ttl_seconds` | `600` | 主缓存空闲 TTL，命中后刷新 |
| `cache_max_entries` | `32` | 主缓存最多保留的快照数 |
| `cache_max_bytes` | `268435456`（256 MiB） | 主缓存总大小上限 |

文件数、大小和缓存容量须为正数。过期缓存会在后续缓存活动时清理；容量不足按 LRU 淘汰未占用条目。服务端完整性 API 的限制也会生效，调大 Bot 限额不能绕过服务端限制。

## 子 Agent 与常见问题

子 Agent 使用工作区本地 Skill 摘要和 Always 内容。当前子 Agent 不注册 OpenViking 工具，因此不发现、激活或继承主 Agent 的远程 Skill 运行时与快照。

| 现象或错误 | 检查方式 |
|------------|----------|
| 本地 Skill 未出现在摘要 | 检查活动 Workspace、直接子目录中的 `SKILL.md`，以及 Bot 进程的命令和环境变量 |
| 无远程候选 | 检查连接、渠道开关、工具禁用、检索分数/超时、同名本地目录；发现失败时继续使用本地上下文 |
| `SKILL_NOT_ACTIVE` | 先用 `openviking_multi_read` 读取该包的 `SKILL.md` |
| `SKILL_TOOL_NOT_ALLOWED` | 检查所有已激活 Skill 的权限交集，以及 Bash 命令约束 |
| `SKILL_CAPABILITY_UNAVAILABLE` | 在实际执行沙箱提供声明的命令/环境变量，确认沙箱可用 |
| `SKILL_RESOURCE_NOT_FOUND` / `SKILL_RESOURCE_AMBIGUOUS` | 使用返回的 canonical 资源 URI；普通工作区输入使用 `workspace:` |
| `SKILL_INTEGRITY_UNAVAILABLE` / `SKILL_REVISION_CHANGED` | 确认服务端支持完整性清单；Skill 更新完成后在新 Turn 重新读取 |
| `SKILL_PACKAGE_TOO_LARGE` | 精简包内资源，或核对 Bot 与服务端限制 |

## 实现位置

| 内容 | 路径（相对 `bot/`，另有说明除外） |
|------|------------------------------------------------|
| 本地加载、metadata 和依赖过滤 | `vikingbot/agent/skills.py` |
| 主 Agent / 子 Agent 的 Skill 上下文 | `vikingbot/agent/context.py`、`vikingbot/agent/subagent.py` |
| 请求运行时、激活、策略与资源解析 | `vikingbot/agent/remote_skills.py` |
| 缓存与工作区初始化 | `vikingbot/agent/remote_skill_cache.py`、`vikingbot/sandbox/manager.py` |
| 工具批次、Schema 与调用处理 | `vikingbot/agent/loop.py`、`vikingbot/agent/tools/registry.py` |
| 远程读取与 Experience Hook | `vikingbot/agent/tools/ov_file.py`、`vikingbot/hooks/builtins/openviking_hooks.py` |
| 配置默认值 | `vikingbot/config/schema.py` |
| OpenViking frontmatter 解析 | 仓库根目录 `openviking/core/skill_loader.py` |

## 相关文档

- [Agent 能力体系](./02-agent-capabilities.md)
- [VikingBot 与 OpenViking 集成](./04-openviking-integration.md)
- [OpenViking Skills API](../../../../docs/zh/api/04-skills.md)
