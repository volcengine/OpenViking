# 服务端配置字段

首次配置建议使用 `openviking-server init`，保存后运行 `openviking-server doctor`。

OpenViking 服务端读取 `ov.conf`。默认路径是：

```text
~/.openviking/ov.conf
```

也可以通过环境变量或启动参数指定其他文件：

```bash
export OPENVIKING_CONFIG_FILE=/path/to/ov.conf
openviking-server --config /path/to/ov.conf
```

服务端启动时读取配置。修改模型、检索、存储或 `server` 配置后，需要重启服务；重启后建议运行 `openviking-server doctor`。

通过单 worker 的 `openviking-server` 启动时，Studio 的「服务端配置」页面提供「保存并重启」。重启能力和实例标识复用现有 ROOT 配置读取接口（`GET /api/v1/admin/configuration?source=file`）返回；只有 ROOT 可以请求重启（`POST /api/v1/admin/restart`，请求体为 `{"revision":"<已保存文件的版本>"}`）。服务校验当前文件和版本后返回 HTTP 202，等待现有请求结束，停止其管理的 Bot，再使用原解释器、启动参数、环境变量和工作目录替换当前进程。页面确认新的服务实例恢复后才显示成功。单独点击「保存配置」仍只保存文件。

多 worker 或嵌入式 ASGI 启动不支持页面重启，需要通过部署平台重启。配置通过校验后，外部依赖仍可能导致启动失败。如果修改了地址、端口或 ROOT 凭证，请更新 Studio 连接设置。账号和集群覆盖配置仍优先于文件默认配置。

配置含环境变量引用时，Studio 表单只读，请使用文件模式编辑原文。带引号或未带引号的引用、转义和格式按提交内容保存。Windows `%VAR%` 引用和美元/百分号字面值（包括 `\u0024`、`\u0025`）也使用文件模式，避免表单序列化引入新的引用。

## 配置结构

下面只展示常用顶层分组，不是可直接运行的完整配置。首次启动可从本页的[最小示例](#最小示例)开始，填写实际模型与凭据；后续示例中的片段应合并到同一个 `ov.conf`。

```json
{
  "embedding": {},
  "vlm": {},
  "query_planner": {},
  "rerank": {},
  "rules": {},
  "retrieval": {},
  "storage": {},
  "server": {},
  "memory": {},
  "parsers": {},
  "encryption": {},
  "log": {},
  "telemetry": {}
}
```

未配置的可选模块使用默认值。`ov.conf` 及账户配置会忽略未知字段（`gateway` 部分除外），兼容旧版本遗留配置；已知字段仍校验类型和取值。字段名拼写错误也会被忽略，但服务端会输出 WARNING，逐项列出未被采用的字段。

## 顶层配置

| 配置项 | 类型 / 可选值 | 默认值 | 作用 |
|---|---|---|---|
| `default_account` | string | `"default"` | Service context 使用的默认账号 |
| `default_user` | string | `"default"` | Service context 使用的默认用户 |
| `embedding` | object | 内置本地 Dense 模型 | 向量化模型和稀疏/混合检索配置；默认使用 `local` / `bge-small-zh-v1.5-f16` |
| `vlm` | object | 空配置 | 内容理解、摘要和记忆抽取使用的模型；使用相关能力前需要配置可用模型 |
| `query_planner` | object / `null` | `null` | 检索意图分析和召回改写使用的模型。未配置或为空时回退到 `vlm`；`auto` 模式的召回改写要求配置集群级 `query_planner`，或账户级 `query_planner` / `vlm` 覆盖项 |
| `rerank` | object | disabled | 检索结果重排模型 |
| `rules` | object | 空配置 | 规则编译和适用性判断使用的可选专用模型 |
| `retrieval` | object | 见下表 | 检索排序和意图分析策略 |
| `grep` | object | 内置默认值 | 文本搜索引擎配置 |
| `glob` | object | 内置默认值 | 路径模式匹配引擎配置 |
| `storage` | object | 本地存储 | 工作目录、文件系统和向量数据库 |
| `queue_workers` | object | 见下表 | QueueFS 消费 worker 的运行时并发配置 |
| `server` | object | 本地开发模式 | HTTP 服务、鉴权、上传和可观测性 |
| `memory` | object | 见下表 | 会话提交后的记忆与技能抽取 |
| `parsers` | object | 各解析器默认值 | PDF、代码、图片、音视频等解析行为 |
| `semantic` | object | 内置默认值 | abstract 和 overview 的生成限制 |
| `parser_api` | object | disabled | 第三方文件解析 API |
| `compile_api` | object | disabled | 外部 Compile 任务 API |
| `connector` | object | disabled | 外部 Connector 数据导入服务 |
| `encryption` | object | disabled | 文件和敏感字段加密 |
| `git` | object | local | 版本管理后端，可使用 `local` 或 `s3` |
| `log` | object | 控制台日志 | 日志级别、格式和文件输出 |
| `telemetry` | object | disabled | OpenTelemetry trace 上报 |
| `oauth` | object | disabled | MCP OAuth 2.1 配置 |
| `gateway` | object | disabled | OpenViking 网关配置，网关为使用 API Key 的模型客户端补充 OpenViking 记忆。这一部分的未知字段会直接报错，见[配置参考](../gateway/13-configuration.md#configuration-reference) |
| `prompts` | object | 内置模板 | 自定义 Prompt 模板目录 |
| `ingest` | object | 内置默认值 | 会话日志导入配置 |
| `output_language_override` | string | `""` | 强制摘要和记忆输出语言；空值表示自动识别 |
| `allow_private_networks` | boolean | `false` | 是否允许抓取内网或私有地址资源 |

`auto_generate_l0`、`auto_generate_l1`、`default_search_mode` 和 `default_search_limit` 是已弃用的兼容字段。旧配置文件仍可加载这些字段，但它们不会影响运行时行为。

## 模型配置

API 型 `embedding`、`vlm`、`query_planner` 和 `rerank` 配置会复用部分字段名，但各模块使用独立 schema。请只使用下表中对应模块支持的字段。

```json
{
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "model": "doubao-embedding-vision-251215",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3",
      "api_key": "<your-ark-api-key>",
      "dimension": 1024,
      "input": "multimodal"
    }
  },
  "vlm": {
    "provider": "volcengine",
    "model": "doubao-seed-2-0-code-preview-260215",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3",
    "api_key": "<your-ark-api-key>",
    "temperature": 0,
    "max_retries": 3,
    "thinking": false
  },
  "query_planner": {
    "provider": "volcengine",
    "model": "doubao-seed-2-0-code-preview-260215",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3",
    "api_key": "<your-ark-api-key>",
    "thinking": false
  },
  "rerank": {
    "provider": "vikingdb",
    "ak": "<your-volcengine-ak>",
    "sk": "<your-volcengine-sk>",
    "host": "api-vikingdb.vikingdb.cn-beijing.volces.com",
    "model_name": "doubao-seed-rerank",
    "model_version": "251028",
    "threshold": 0.1,
    "max_input_tokens": 0
  }
}
```

| 字段 / 路径 | 适用模块 | 作用 |
|---|---|---|
| `provider`、`model`、`api_base`、`api_key` | Embedding、VLM、Query Planner、Rerank | 模型服务、地址和凭证 |
| `api_version` | Embedding、VLM、Query Planner | Azure 等服务的 API 版本 |
| `extra_headers` | Embedding、VLM、Query Planner、Rerank | 附加请求头 |
| `extra_request_body` | VLM、Query Planner | 附加的 Completion 请求参数 |
| `extra_body` | `embedding.dense` / `sparse` / `hybrid` | 附加的 Embedding 请求参数 |
| `timeout` | VLM、Query Planner、Rerank | 单次请求超时，单位为秒 |
| `embedding.max_retries`、`vlm.max_retries`、`query_planner.max_retries` | Embedding、VLM、Query Planner | 请求失败重试次数；Rerank 没有 `max_retries` 字段 |

### `embedding.dense`

| 字段 | 类型 / 可选值 | 作用 |
|---|---|---|
| `provider` | `openai`、`volcengine`、`azure`、`ollama`、`local` 等 | Dense Embedding 服务 |
| `dimension` | integer，`> 0` | 向量维度，必须与模型输出及已有集合一致 |
| `input` | `"text"` / `"multimodal"` | 输入类型 |
| `encoding_format` | `"float"` / `"base64"` | OpenAI 兼容接口的向量编码格式 |

更换模型或 `dimension` 可能与已有向量集合不兼容，需要迁移或重建索引。

### `rules`

Compiled Rule 的可选模型与生命周期配置：

```json
{
  "rules": {
    "jev_rule_matcher": {
      "provider": "typesafe",
      "api_key": "${JEV_API_KEY}"
    },
    "compiler": {
      "provider": "openai",
      "model": "gpt-4o"
    },
    "compiler_context_window": 0
  }
}
```

| 字段 | 类型 / 可选值 | 默认值 | 作用 |
|---|---|---|---|
| `jev_rule_matcher` | Jev Noul 配置 / `null` | `null` | Scenario 过滤和 Rule 适用性判断使用的 Jev Noul 模型 |
| `compiler` | VLM 配置 / `null` | `null` | 专用 Rule Compiler |
| `compiler_context_window` | `0` 或 integer `>= 2048` | `0` | Compiler 上下文窗口；`0` 表示自动探测并使用保守回退值 |
| `scenario_irrelevance_threshold` | number，`0.5`-`1.0` | `0.9` | Scenario 无关概率达到该值时跳过 |
| `archived_retention_days` | integer，`>= 1` | `30` | archived Rule 的物理删除保留天数 |
| `archived_cleanup_interval_seconds` | integer，`>= 60` | `2592000` | archived Rule 清理周期 |
| `archived_cleanup_enabled` | boolean | `true` | 是否定期清理 archived Rule |

Rule 编译始终以 `thinking=true` 调用专用 Compiler 或回退的账户默认 VLM，
并覆盖其配置默认值。请使用支持思考模式的模型，否则编译可能失败。

#### `rules.jev_rule_matcher`

该配置用于 Scenario 过滤和 Rule 适用性的独立 Jev System One 判断，不用于检索结果重排。

| 字段 | 类型 / 可选值 | 默认值 | 作用 |
|---|---|---|---|
| `provider` | `typesafe`、`vercel`、`openrouter`、`custom` | `typesafe` | 选择 endpoint 和 model 默认值 |
| `api_key` | string | 必填 | 所选 provider 的 API Key |
| `api_base` | URL / `null` | provider 默认值 | 完整的 System One 请求 URL；显式配置的值会原样使用 |
| `model` | string / `null` | provider 默认值 | 模型名或 Endpoint ID；`custom` 必填 |
| `timeout` | number | `30` | HTTP 请求超时秒数 |
| `extra_headers` | object / `null` | `null` | 附加 HTTP 请求头 |
| `log_payloads` | boolean | `false` | 是否记录完整请求和响应；日志可能包含任务与 Rule 内容 |

| Provider | 默认请求 URL | 默认 model | 说明 |
|---|---|---|---|
| `typesafe` | `https://api.typesafe.ai/v1/systemone` | `jev-latest` | TypeSafe 直连 |
| `vercel` | `https://ai-gateway.vercel.sh/typesafe/v1/systemone` | `typesafe-ai/jev` | Vercel AI Gateway 的 TypeSafe 兼容 API |
| `openrouter` | `https://openrouter.ai/api/v1/systemone` | `jev-latest` | OpenRouter System One API |
| `custom` | 无 | 无 | 必须同时配置 `api_base` 和 `model` |

`api_base` 和 `model` 可分别覆盖 provider 默认值。显式配置的 `api_base` 是完整
POST URL，任何 provider（包括 `custom`）都不会修改它或追加后缀。
`extra_headers` 会扩展 provider 请求头，也可以覆盖默认请求头。Rule 匹配固定使用
独立 Noul 判断，因此不再包含 `mode`、`threshold` 或 `max_input_tokens` 配置。
Scenario 过滤阈值由 `rules.scenario_irrelevance_threshold` 配置；
Rule 适用性使用 Rule 搜索请求中的 `rule_probability_threshold`（默认 `0.65`）。

Rule 状态为 `active`、`paused`、`archived`。`PAUSE` 暂停检索但不设置清理期限；
`ARCHIVE` 从 `archived_at` 开始计算保留期；`RESTORE` 将 paused Rule 恢复为
active。编译候选只包含 active 和 paused Rule；archived Rule 视为逻辑删除，不是
编译候选，也无法通过 Rule 编译恢复。`ARCHIVE` 必须由用户指令明确表达删除、丢弃
或移入回收站；不包含删除语义的停用请求使用 `PAUSE`。

Rule 按 Scenario 直接存放在 `memories/rules/<scenario_id>/`：`abstract.md`
保存场景元信息，`<atomic_id>.md` 保存规则。存储格式见
[规则（Beta）](../concepts/17-rule-scenarios.md)。

`jev_rule_matcher` 未配置、不可用、调用失败或返回无效结果时，Scenario 和 Rule 判断
直接放行，不调用 VLM。Scenario 诊断记录 `decision=pass`；Rule 返回 `judge=pass`
和 warning，以 `1.0` 作为通过阈值的放行分数，
该值不是模型评估的适用概率。Agent 仍需根据具体事实核验适用条件和例外。
规则编译从 `compiler` 回退到账户默认 VLM。原 `rule_matcher` 配置已移除，不再读取。

Rule 的 `applies_when` 可以为空，默认空值；为空时表示任何情况都适用，除非
`exception` 条件成立。没有适用条件和例外的候选 Rule 适用概率直接取 `1.0`。
Compiler 会优先忠实提炼原文中的对象、操作、前提和场景，尽量生成非空且可判定的
`applies_when`，不得编造条件或丢弃原文已有的限制。空条件不表示未知或不适用。

### `rerank`

| 字段 | 类型 / 可选值 | 默认值 | 作用 |
|---|---|---|---|
| `provider` | `vikingdb`、`cohere`、`openai`、`litellm`、`jev` / `null` | `null` | Rerank 服务类型；省略时根据凭证字段推断 |
| `model` | string / `null` | `null` | OpenAI 兼容、LiteLLM 或 Jev Rerank 模型 |
| `mode` | `noul`、`choice` 或 `null` | `noul` | Jev Rerank 模式；`null` 同样使用 `noul` |
| `threshold` | number | `0.1` | 判定结果相关的最低 Rerank 分数；只过滤模型产出的 Rerank 分数，不过滤向量分数；请求中的 `score_threshold` 优先 |
| `max_input_tokens` | integer；`0` 或 `>= 128` | `0` | 每个 query-document pair 的最大估算 token；`0` 表示不截断 |
| `log_payloads` | boolean | `false` | 记录完整 rerank 请求和响应；日志可能包含 query 和文档内容 |

Rerank 没有单独的 `enabled` 字段；配置了对应 provider 所需的凭证后才会启用。

`jev` 通过现有 `api_base` 和 `model` 字段同时支持 TypeSafe 直连（`https://api.typesafe.ai/v1/systemone`，模型 `jev-latest`）和 Vercel AI Gateway 的 TypeSafe 兼容端点（`https://ai-gateway.vercel.sh/typesafe/v1/systemone`，模型 `typesafe-ai/jev`），两者协议相同。Rerank 的 `api_base` 可以是服务根地址或完整 System One URL；缺少 `/v1/systemone` 时会自动追加。默认 `noul` 模式独立评估每个候选；可选的 `choice` 模式横向比较所有候选并返回相对概率，使用时应将 `threshold` 设为 `0`。显式指定 `provider` 时必须提供该 provider 所需的凭证：`vikingdb` 需要 `ak` 和 `sk`，`cohere` 和 `jev` 需要 `api_key`，`openai` 需要 `api_key` 和 `api_base`，`litellm` 需要 `model`。凭证不全的配置在加载时即被拒绝。

## 检索配置

```json
{
  "retrieval": {
    "enable_intent": true,
    "apply_rerank_to_context": false
  }
}
```

### `retrieval`

| 字段 | 类型 / 可选值 | 默认值 | 作用 |
|---|---|---|---|
| `enable_intent` | boolean | `true` | 有 `session_id` 时是否进行意图分析和查询规划 |
| `apply_rerank_to_context` | boolean | `false` | 是否用已配置的 `rerank` 模型重排 `search(mode="context")` 的候选 |

Search 和 Find 请求的默认 `limit` 为 `10`，可以在每次 API 或 SDK 请求中覆盖。`retrieval.enable_intent` 控制带 Session 的 Search 是否执行 LLM 查询规划；只有配置了可用的 `rerank` provider 时才会执行结果重排。Context 模式使用 Find 的向量分数召回；开启 `retrieval.apply_rerank_to_context` 且 `rerank` 可用时，会在配额和 token 预算之前，用原始查询及其扩展查询对合并后的候选统一重排一次，每个请求多一次 Rerank 调用。

## 存储配置

```json
{
  "storage": {
    "workspace": "./data",
    "skip_process_lock": false,
    "agfs": {
      "backend": "local"
    },
    "vectordb": {
      "backend": "local"
    },
    "parse_output": {
      "mode": "agfs"
    }
  },
  "parsers": {
    "download_temp_dir": "/data/openviking/parser-tmp"
  }
}
```

### `storage`

| 字段 | 类型 / 常用值 | 默认值 | 作用 |
|---|---|---|---|
| `workspace` | path | `"./data"` | OpenViking 工作目录 |
| `agfs.backend` | `local`、`memory`、`s3` | `local` | 文件与元数据存储后端 |
| `agfs.pathlock.provider` | `filesystem`、`memory`、`sqlite`、`cache` | `filesystem` | 路径锁存储 |
| `agfs.pathlock.sqlite_path` | 本地文件绝对路径 | 使用 `sqlite` 时为 `{workspace}/_system/pathlock/pathlock.db` | SQLite 共享锁数据库，可显式覆盖 |
| `vectordb.backend` | `local`、`cuvs`、`http`、`volcengine`、`vikingdb` | `local` | 向量数据库后端 |
| `vectordb.dimension` | integer | 跟随 Embedding | 向量集合维度 |
| `parse_output.mode` | `agfs`、`local` | `agfs` | parser 中间产物的存储后端 |
| `parse_output.local_root` | 路径或 `null` | 系统临时目录 | local parser artifact 的根目录 |
| `skip_process_lock` | boolean | `false` | 是否跳过 workspace 进程锁；仅在明确接受并发写风险时启用 |

同机本地磁盘上的路径锁可通过 SQLite 避免递归扫描业务目录。将 `storage.agfs.pathlock` 设置为
`{"provider":"sqlite"}`。数据库默认位于
`{storage.workspace}/_system/pathlock/pathlock.db`，初始化运行时 binding 时
根据最终 workspace 解析为绝对路径。显式指定的绝对路径 `sqlite_path` 优先，
数据库父目录会自动创建。
直接通过 `RAGFSBindingClient(config_path=..., config={"service_name": ...})` 构造时，
省略 `sqlite_path` 或将其设为 `null` 也会使用上述默认路径。
所有参与进程必须访问同一个实际的本地数据库文件，并使用相同的顶层 `service_name` 和锁过期配置。
同一主机上的容器必须共享该本地文件；不同容器文件系统中相同的路径字符串指向独立数据库，
不能互相协调路径锁。
SQLite 是锁状态的唯一存储，通过路径索引查询，并在事务中原子检查及获取 Exact/Tree 锁；
数据库阻塞操作在异步执行器之外运行。
SQLite 每次尝试对瞬时数据库写竞争最多等待 100 毫秒，即使逻辑锁等待超时为零也会执行该等待。
仅 Busy 会在 provider 内重试，最多尝试 3 次，重试前分别退避 1、4 毫秒；
数据库等待可累计约 300 毫秒，另加 5 毫秒退避及执行、调度开销。
重试耗尽仍向 manager 返回 Busy；逻辑锁 Conflict 不重试。
WAL 使用 `synchronous=FULL` 保证崩溃后已提交锁记录的持久性，
代价是比 `NORMAL` 更多的同步 I/O。
租约心跳在一次 SQLite 事务中批量刷新所属路径，大租约按有限大小分批执行。
`stale_locks_removed` 包含 SQLite/Redis 原子加锁事务实际回收的锁数量，
失败并回滚的事务不会增加计数。

SQLite 加锁会完整删除其他 owner 持有、已过期且与本次请求冲突的记录：
目标路径、祖先 Tree 锁，以及请求 Tree 锁时的全部后代锁。
清理与加锁在同一事务内完成，旧 owner 在接管后无法通过续租恢复这些记录。
全局增量清理每次加锁最多删除 256 条记录；冲突记录清理不受此上限限制，
因此过期子树较大时仍会增加事务开销。

不能将数据库放在 EFS、NFS 或 SMB 上。SQLite WAL 要求参与者位于同一主机，不支持网络文件系统；
频繁更新锁记录还会增加同步 I/O。如果 `storage.workspace` 位于 EFS，默认数据库路径也会落在 EFS。
将数据库改到各容器本地磁盘会形成独立的锁状态，无法协调多个副本。

多个容器或主机共享 EFS 时，使用共享 Redis PathLock：
设置 `storage.agfs.pathlock.provider="cache"` 和顶层 `cache.provider="redis"`。
所有副本必须连接同一个 Redis 部署，并使用相同的顶层 `service_name` 和锁过期配置。
参见[共享 Redis 配置示例](../guides/01-configuration.md)。
共享 QueueFS 也需选择适合该部署的后端，例如 `storage.agfs.queuefs.backend="cache"`；
仅修改 PathLock 不会改变默认的 SQLite 队列后端。

切换 provider 前需停止全部参与进程，
SQLite 与 filesystem 锁文件不共享协议。进程运行期间不能删除数据库及其 WAL/SHM 文件。
崩溃进程的锁在 `lock_expire_secs`（默认 30 秒）后可被接管。
此配置不会改变 workspace 进程锁或其他后端的并发限制。

远程存储后端还需要配置 endpoint、bucket/collection、鉴权和超时等字段。完整后端示例见[配置指南](../guides/01-configuration.md#storage)。

`parse_output.mode=local` 可避免把 parser 中间产物写入共享 AGFS。当前 worker
必须在下游任务入队前把所需字节提交到正式资源树。产物会在内容提交后清理；
请为 `local_root` 预留足够空间以容纳并发导入。

### `parsers`

| 字段 | 类型 / 常用值 | 默认值 | 作用 |
|---|---|---|---|
| `download_temp_dir` | 路径或 `null` | 系统临时目录 | 内置 parser 下载文件、Git checkout 和压缩包解压使用的临时目录 |

配置后，parser 会在该目录下创建带有 `ov_git_`、`ov_zip_` 等前缀的临时目录；未配置时继续使用系统临时目录。

## 队列 Worker 配置

### `queue_workers.external_parse`

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---:|---|
| `max_concurrent` | integer | `4` | 同时消费的完整 ExternalParse 作业数，必须大于 `0`；修改后需重启服务 |

该配置控制队列作业并发，不等同于 `vlm.media.max_concurrent` 的音视频 VLM 调用并发，也不限制 Understanding API 的单独 HTTP 请求数。

### `queue_workers.add_resource`

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---:|---|
| `max_concurrent` | integer | `4` | 同时消费的完整 AddResource 作业数，必须大于 `0`；修改后需重启服务 |
| `file_operation_concurrency` | integer | `16` | 单个 AddResource 作业内文件级提交和 fallback 比较操作的最大并发数，必须大于 `0`；修改后需重启服务 |
| `file_vectorization_concurrency` | integer | `8` | 当目录 AddResource 使用 `processing_mode="vectors_only"` 时，单个作业内并发读取、准备并入队的文件数，必须大于 `0`；超过内部安全上限 `64` 的值会被截断；修改后需重启服务 |

`max_concurrent` 控制相互独立的 AddResource 作业并发，`file_operation_concurrency` 控制单个 AddResource 作业内文件提交和 fallback 比较操作的并发，`file_vectorization_concurrency` 控制单个 vectors-only 目录作业内的文件并发。

### `queue_workers.reindex`

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---:|---|
| `max_concurrent` | integer | `4` | 同时消费的完整 Reindex root 作业数，必须大于 `0`；修改后需重启服务 |

该配置限制相互独立的异步 reindex 请求并发。URI 范围重叠的请求仍由 path lock 保护；VLM 和 embedding 仍分别遵守各自的并发限制。

### `queue_workers.session_commit`

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---:|---|
| `max_concurrent` | integer | `8` | 同时消费的 SessionCommit 作业数，必须大于 `0`；修改后需重启服务 |

### `queue_workers.external_task`

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---:|---|
| `max_concurrent` | integer | `10` | 同时消费的外部异步任务数，必须大于 `0`；修改后需重启服务 |

## Compile API 配置

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---:|---|
| `base_url` | string | `""` | 外部服务地址，必须包含 `http://` 或 `https://`；非空即启用外部 Compile |
| `gateway_token` | string | `""` | OV 调用 Compile Gateway 使用的可选服务凭证 |
| `http_timeout_seconds` | number | `10` | 单次 HTTP 请求超时 |
| `poll_interval_ms` | integer | `30000` | 外部任务状态轮询间隔 |

配置 `base_url` 后，OV 通过 `X-API-Key` 传递当前用户的 OV API Key；仅在配置 `gateway_token` 时发送 `X-Gateway-Token`。

## Reindex 配置

### `reindex`

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---:|---|
| `file_vectorization_concurrency` | integer | `8` | 单个 resource/skill reindex 任务内并发读取、计算指纹和准备文件的数量，必须大于 `0`；超过内部安全上限 `64` 的值会被截断；修改后需重启服务 |

## HTTP 服务配置

```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 1933,
    "workers": 1,
    "executor_threads": 0,
    "auth_mode": "dev",
    "cors_origins": ["http://localhost:5173"],
    "profile_enabled": false,
    "temp_upload": {
      "default_mode": "local"
    }
  }
}
```

### `server`

| 字段 | 类型 / 可选值 | 默认值 | 作用 |
|---|---|---|---|
| `host` | IP / hostname | `"127.0.0.1"` | HTTP 监听地址 |
| `port` | integer | `1933` | HTTP 监听端口 |
| `workers` | integer | `1` | 服务进程数量 |
| `executor_threads` | 非负整数 | `0` | 每个服务进程的 asyncio 默认 executor 最大线程数；`0` 表示沿用 Python 默认策略 |
| `timeout_keep_alive` | integer（秒） | `5` | 空闲 HTTP keep-alive 超时；应调大到超过上游空闲连接寿命 |
| `auth_mode` | `dev`、`api_key`、`trusted` / `null` | `null` | 鉴权模式；空值根据 `root_api_key` 自动判断 |
| `root_api_key` | string / `null` | `null` | Root API Key；配置后默认启用 `api_key` 模式 |
| `cors_origins` | string[] | `["*"]` | 允许的跨域来源 |
| `profile_enabled` | boolean | `false` | 是否允许请求返回性能 profile |
| `with_bot` | boolean | `false` | 是否启用 VikingBot API 代理 |
| `bot_api_url` | URL | `http://localhost:18790` | VikingBot OpenAPI 地址 |
| `public_base_url` | URL / `null` | `null` | 外部访问使用的服务基准地址 |
| `upload_signed_ttl_seconds` | integer | `600` | 签名上传、下载 URL 有效期 |
| `temp_upload.default_mode` | `"local"` / `"shared"` | `"local"` | 临时上传存储模式 |

### 文件加密与 API Key 哈希

文件加密和 API Key 哈希在顶层 `encryption` 中配置，不属于 `server`：

```json
{
  "encryption": {
    "enabled": false,
    "api_key_hashing": {
      "enabled": false
    }
  }
}
```

| 字段 | 类型 / 可选值 | 默认值 | 作用 |
|---|---|---|---|
| `encryption.enabled` | boolean | `false` | 是否启用文件级 AES 加密 |
| `encryption.api_key_hashing.enabled` | boolean | `false` | 是否使用 Argon2id 保存 API Key |

Provider 和密钥管理配置见[加密指南](../guides/08-encryption.md)。

### 鉴权模式

| 值 | 使用场景 |
|---|---|
| `dev` | 仅监听本机地址的开发环境，不要求 API Key |
| `api_key` | 服务端校验 root/user/admin key |
| `trusted` | 由受信任网关注入 account/user 身份 |

## 记忆配置

```json
{
  "memory": {
    "custom_templates_dir": "",
    "experimental_memory_switch": false,
    "eager_prefetch": true,
    "prefetch_search_topn": 5,
    "extraction_enabled": true,
    "session_skill_extraction_enabled": false,
    "link_enabled": false
  }
}
```

### `memory`

| 字段 | 类型 / 可选值 | 默认值 | 作用 |
|---|---|---|---|
| `custom_templates_dir` | path | `""` | 自定义记忆模板目录；同名 `memory_type` 覆盖已加载模板 |
| `experimental_memory_switch` | boolean | `false` | 是否启用实验性记忆模板 |
| `eager_prefetch` | boolean | `true` | 是否在抽取前预取并读取记忆内容 |
| `prefetch_search_topn` | integer，`>= 1` | `5` | 预取时读取的检索结果数量 |
| `extraction_enabled` | boolean | `true` | session commit 时是否抽取长期记忆 |
| `session_skill_extraction_enabled` | boolean | `false` | 是否同时抽取可复用 Skill |
| `link_enabled` | boolean | `false` | 是否生成和解析记忆链接 |

自动提交还需要单独设置策略：记忆抽取开关不会让会话自动提交。详见[配置指南](../guides/01-configuration.md)的 `memory.session_auto_commit` 和[会话 API](../api/05-sessions.md)。模板目录的加载顺序和生效方式见 [Prompt 指南](../guides/10-prompt-guide.md)。

## 解析器配置

解析器放在 `parsers` 下：

```json
{
  "parsers": {
    "pdf": {},
    "code": {
      "code_summary_mode": "ast",
      "extract_functions": true,
      "extract_classes": true,
      "max_token_limit": 50000
    },
    "image": {},
    "audio": {},
    "video": {},
    "markdown": {},
    "anydoc": {
      "enabled": true
    },
    "html": {},
    "text": {},
    "directory": {
      "preserve_structure": true,
      "max_files": null,
      "max_depth": 10,
      "max_concurrent": 4
    },
    "feishu": {
      "domain": "https://open.feishu.cn",
      "max_rows_per_sheet": 1000,
      "max_records_per_table": 1000,
      "download_images": true
    },
    "webfeed": {}
  }
}
```

`parsers.directory.max_files` 默认是 `null`，表示不限文件数；
设为正整数可限制单次目录导入的文件数。

`parsers.directory.max_concurrent` 由服务事件循环中的所有目录导入共享。默认值为
`4` 时，单个目录可以并发执行 4 个 Understanding 任务；多个目录同时导入时，合计仍最多
执行 4 个。

启用 Understanding 目录路由时，`max_files` 和 `max_depth` 才约束目录导入。每次
`DirectoryParser` 扫描会在提交该层 Understanding 请求前独立应用限制；嵌套 ZIP 会启动
新的目录扫描，不与外层共享文件数量和深度预算。关闭 Understanding 时，OpenViking
原生目录解析不应用这两个限制。

客户端导入本地目录时，完整目录 ZIP 受 `/resources/temp_upload` 上传大小限制。ZIP
解压后，`DirectoryParser` 不再设置统一的单文件字节限制；每个入选文件遵循对应内置
Parser 或 Understanding API 后端自身的限制和上传行为。

| 配置项 | 作用 |
|---|---|
| `pdf` | PDF 文本、图片和版面解析 |
| `code` | 代码仓库文件类型、忽略规则和安全限制 |
| `image` | 图片理解和 OCR |
| `audio`、`video` | 音视频内容解析 |
| `markdown`、`html`、`text` | 文本文档分段 |
| `anydoc` | Office 和 EPUB 转换；`enabled=false` 时拒绝这些格式 |
| `directory` | 目录扫描和忽略规则 |
| `feishu` | 飞书文档访问与解析 |
| `webfeed` | Sitemap、RSS 和 Atom 导入 |

各模型 provider、解析器、存储后端和加密后端包含较多专用字段，完整字段表和配置示例见[配置指南](../guides/01-configuration.md)。

## 最小示例

```json
{
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "model": "doubao-embedding-vision-251215",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3",
      "api_key": "<your-ark-api-key>",
      "dimension": 1024,
      "input": "multimodal"
    }
  },
  "vlm": {
    "provider": "volcengine",
    "model": "doubao-seed-2-0-code-preview-260215",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3",
    "api_key": "<your-ark-api-key>",
    "thinking": false
  },
  "storage": {
    "workspace": "./data"
  },
  "server": {
    "host": "127.0.0.1",
    "port": 1933
  }
}
```
