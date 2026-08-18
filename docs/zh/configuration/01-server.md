# 服务端配置

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

## 配置结构

```json
{
  "embedding": {},
  "vlm": {},
  "query_planner": {},
  "rerank": {},
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

未配置的可选模块使用默认值。`ov.conf` 不允许未知字段，字段名写错时服务端会拒绝加载。

## 顶层配置

| 配置项 | 类型 / 可选值 | 默认值 | 作用 |
|---|---|---|---|
| `default_account` | string | `"default"` | Service context 使用的默认账号 |
| `default_user` | string | `"default"` | Service context 使用的默认用户 |
| `embedding` | object | 内置本地 Dense 模型 | 向量化模型和稀疏/混合检索配置；默认使用 `local` / `bge-small-zh-v1.5-f16` |
| `vlm` | object | 空配置 | 内容理解、摘要和记忆抽取使用的模型；使用相关能力前需要配置可用模型 |
| `query_planner` | object / `null` | `null` | 检索意图分析模型；未配置时回退到 `vlm` |
| `rerank` | object | disabled | 检索结果重排模型 |
| `retrieval` | object | 见下表 | 检索排序和意图分析策略 |
| `grep` | object | 内置默认值 | 文本搜索引擎配置 |
| `storage` | object | 本地存储 | 工作目录、文件系统和向量数据库 |
| `queue_workers` | object | 见下表 | QueueFS 消费 worker 的运行时并发配置 |
| `server` | object | 本地开发模式 | HTTP 服务、鉴权、上传和可观测性 |
| `memory` | object | 见下表 | 会话提交后的记忆与技能抽取 |
| `parsers` | object | 各解析器默认值 | PDF、代码、图片、音视频等解析行为 |
| `semantic` | object | 内置默认值 | abstract 和 overview 的生成限制 |
| `parser_api` | object | disabled | 第三方文件解析 API |
| `connector` | object | disabled | 外部 Connector 数据导入服务 |
| `encryption` | object | disabled | 文件和敏感字段加密 |
| `git` | object | local | 版本管理后端，可使用 `local` 或 `s3` |
| `log` | object | 控制台日志 | 日志级别、格式和文件输出 |
| `telemetry` | object | disabled | OpenTelemetry trace 上报 |
| `oauth` | object | disabled | MCP OAuth 2.1 配置 |
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

### `rerank`

| 字段 | 类型 / 可选值 | 默认值 | 作用 |
|---|---|---|---|
| `provider` | `vikingdb`、`cohere`、`openai`、`litellm` / `null` | `null` | Rerank 服务类型；省略时根据凭证字段推断 |
| `model` | string / `null` | `null` | OpenAI 兼容或 LiteLLM Rerank 模型 |
| `threshold` | number | `0.1` | 判定结果相关的最低分数 |
| `max_input_tokens` | integer；`0` 或 `>= 128` | `0` | 每个 query-document pair 的最大估算 token；`0` 表示不截断 |

Rerank 没有单独的 `enabled` 字段；配置了对应 provider 所需的凭证后才会启用。

## 检索配置

```json
{
  "retrieval": {
    "hotness_alpha": 0,
    "score_propagation_alpha": 1,
    "enable_intent": true
  }
}
```

### `retrieval`

| 字段 | 类型 / 可选值 | 默认值 | 作用 |
|---|---|---|---|
| `hotness_alpha` | number，`0`–`1` | `0` | 热度分数权重；`0` 表示关闭热度加权 |
| `score_propagation_alpha` | number，`0`–`1` | `1` | 层级检索时子结果自身分数的权重 |
| `enable_intent` | boolean | `true` | 有 `session_id` 时是否进行意图分析和查询规划 |

Search 和 Find 请求的默认 `limit` 为 `10`，可以在每次 API 或 SDK 请求中覆盖。`retrieval.enable_intent` 控制带 Session 的 Search 是否执行 LLM 查询规划；只有配置了可用的 `rerank` provider 时才会执行结果重排。

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
    }
  }
}
```

### `storage`

| 字段 | 类型 / 常用值 | 默认值 | 作用 |
|---|---|---|---|
| `workspace` | path | `"./data"` | OpenViking 工作目录 |
| `agfs.backend` | `local`、`memory`、`s3` | `local` | 文件与元数据存储后端 |
| `vectordb.backend` | `local`、`cuvs`、`http`、`volcengine`、`vikingdb` | `local` | 向量数据库后端 |
| `vectordb.dimension` | integer | 跟随 Embedding | 向量集合维度 |
| `skip_process_lock` | boolean | `false` | 是否跳过 workspace 进程锁；仅在明确接受并发写风险时启用 |

远程存储后端还需要配置 endpoint、bucket/collection、鉴权和超时等字段。完整后端示例见[配置指南](../guides/01-configuration.md#storage)。

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

### `queue_workers.session_commit`

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---:|---|
| `max_concurrent` | integer | `8` | 同时消费的 SessionCommit 作业数，必须大于 `0`；修改后需重启服务 |

## HTTP 服务配置

```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 1933,
    "workers": 1,
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
| `timeout_keep_alive` | integer（秒） | `5` | 空闲 HTTP keep-alive 超时；应调大到超过上游空闲连接寿命 |
| `auth_mode` | `dev`、`api_key`、`trusted` / `null` | `null` | 鉴权模式；空值根据 `root_api_key` 自动判断 |
| `root_api_key` | string / `null` | `null` | Root API Key；配置后默认启用 `api_key` 模式 |
| `cors_origins` | string[] | `["*"]` | 允许的跨域来源 |
| `profile_enabled` | boolean | `false` | 是否允许请求返回性能 profile |
| `with_bot` | boolean | `false` | 是否启用 VikingBot API 代理 |
| `bot_api_url` | URL | `http://localhost:18790` | VikingBot OpenAPI 地址 |
| `public_base_url` | URL / `null` | `null` | 外部访问使用的服务基准地址 |
| `upload_signed_ttl_seconds` | integer | `600` | 签名上传 URL 有效期 |
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
    "link_enabled": false,
    "v2_lock_retry_interval_seconds": 0.2,
    "v2_lock_max_retries": 0
  }
}
```

### `memory`

| 字段 | 类型 / 可选值 | 默认值 | 作用 |
|---|---|---|---|
| `custom_templates_dir` | path | `""` | 附加的自定义记忆模板目录 |
| `experimental_memory_switch` | boolean | `false` | 是否启用实验性记忆模板 |
| `eager_prefetch` | boolean | `true` | 是否在抽取前预取并读取记忆内容 |
| `prefetch_search_topn` | integer，`>= 1` | `5` | 预取时读取的检索结果数量 |
| `extraction_enabled` | boolean | `true` | session commit 时是否抽取长期记忆 |
| `session_skill_extraction_enabled` | boolean | `false` | 是否同时抽取可复用 Skill |
| `link_enabled` | boolean | `false` | 是否生成和解析记忆链接 |
| `v2_lock_retry_interval_seconds` | number，`>= 0` | `0.2` | 记忆锁获取失败后的重试间隔 |
| `v2_lock_max_retries` | integer，`>= 0` | `0` | 最大重试次数；`0` 表示不限次数 |

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
    "excel": {},
    "html": {},
    "text": {},
    "directory": {},
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

| 配置项 | 作用 |
|---|---|
| `pdf` | PDF 文本、图片和版面解析 |
| `code` | 代码仓库文件类型、忽略规则和安全限制 |
| `image` | 图片理解和 OCR |
| `audio`、`video` | 音视频内容解析 |
| `markdown`、`html`、`text` | 文本文档分段 |
| `excel` | Excel 工作表解析与分段 |
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
