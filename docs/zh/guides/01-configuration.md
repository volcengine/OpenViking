# 配置

OpenViking 使用 JSON 配置文件（`ov.conf`）进行设置。配置文件支持 Embedding、VLM、Rerank、存储、解析器等多个模块的配置。

首次配置推荐优先使用：

```bash
openviking-server init
openviking-server doctor
```

`openviking-server init` 会分别引导你填写 Embedding 和 VLM 的配置。对于 `OpenAI`、`Volcengine`、`Kimi`、`GLM` 这类 API 型 VLM，按提示填写对应的 VLM API Key；如果要使用 Codex 作为 VLM，请选择 `OpenAI Codex`，向导会自动帮你处理已有 Codex 鉴权的导入，或直接引导你完成登录。

## 快速开始

在项目目录创建 `~/.openviking/ov.conf`：

```json
{
  "storage": {
    "workspace": "./data",
    "vectordb": {
      "name": "context",
      "backend": "local"
    },
    "agfs": {
      "backend": "local"
    }
  },
  "embedding": {
    "dense": {
      "api_base" : "<api-endpoint>",
      "api_key"  : "<your-api-key>",
      "provider" : "<provider-type>",
      "dimension": 1024,
      "model"    : "<model-name>"
    }
  },
  "vlm": {
    "api_base" : "<api-endpoint>",
    "api_key"  : "<your-api-key>",
    "provider" : "<provider-type>",
    "model"    : "<model-name>"
  }
}

```

如果 `provider` 是 `openai-codex`，并且 Codex OAuth 已经就绪，则 `vlm.api_key` 可以省略。

## 配置示例

<details>
<summary><b>火山引擎（豆包模型）</b></summary>

```json
{
  "embedding": {
    "dense": {
      "api_base" : "https://ark.cn-beijing.volces.com/api/v3",
      "api_key"  : "your-volcengine-api-key",
      "provider" : "volcengine",
      "dimension": 1024,
      "model"    : "doubao-embedding-vision-251215",
      "input": "multimodal"
    }
  },
  "vlm": {
    "api_base" : "https://ark.cn-beijing.volces.com/api/v3",
    "api_key"  : "your-volcengine-api-key",
    "provider" : "volcengine",
    "model"    : "doubao-seed-2-0-pro-260215"
  }
}
```

</details>

<details>
<summary><b>OpenAI 模型</b></summary>

```json
{
  "embedding": {
    "dense": {
      "api_base" : "https://api.openai.com/v1",
      "api_key"  : "your-openai-api-key",
      "provider" : "openai",
      "dimension": 1536,
      "model"    : "text-embedding-3-small"
    }
  },
  "vlm": {
    "api_base" : "https://api.openai.com/v1",
    "api_key"  : "your-openai-api-key",
    "provider" : "openai",
    "model"    : "gpt-5.4"
  }
}
```

</details>

<details>
<summary><b>火山引擎 Embedding + Codex VLM</b></summary>

使用 `openviking-server init` 完成 Codex 登录/导入后，再执行 `openviking-server doctor`。

```json
{
  "embedding": {
    "dense": {
      "api_base" : "https://ark.cn-beijing.volces.com/api/v3",
      "api_key"  : "your-volcengine-api-key",
      "provider" : "volcengine",
      "dimension": 1024,
      "model"    : "doubao-embedding-vision-251215"
    }
  },
  "vlm": {
    "provider" : "openai-codex",
    "model"    : "gpt-5.4",
    "api_base" : "https://chatgpt.com/backend-api/codex"
  }
}
```

</details>

<details>
<summary><b>火山引擎 Embedding + Kimi Coding VLM</b></summary>

```json
{
  "embedding": {
    "dense": {
      "api_base" : "https://ark.cn-beijing.volces.com/api/v3",
      "api_key"  : "your-volcengine-api-key",
      "provider" : "volcengine",
      "dimension": 1024,
      "model"    : "doubao-embedding-vision-251215"
    }
  },
  "vlm": {
    "provider" : "kimi",
    "model"    : "kimi-code",
    "api_key"  : "your-kimi-subscription-api-key",
    "api_base" : "https://api.kimi.com/coding"
  }
}
```

`kimi` 会自动应用 Kimi Coding 的默认配置，包括默认的 Kimi Coding User-Agent。

</details>

<details>
<summary><b>火山引擎 Embedding + GLM Coding Plan VLM</b></summary>

```json
{
  "embedding": {
    "dense": {
      "api_base" : "https://ark.cn-beijing.volces.com/api/v3",
      "api_key"  : "your-volcengine-api-key",
      "provider" : "volcengine",
      "dimension": 1024,
      "model"    : "doubao-embedding-vision-251215"
    }
  },
  "vlm": {
    "provider" : "glm",
    "model"    : "glm-4.6v",
    "api_key"  : "your-zai-api-key",
    "api_base" : "https://api.z.ai/api/coding/paas/v4"
  }
}
```

如果 OpenViking 需要处理图片，请使用 `glm-4.6v` 或 `glm-5v-turbo` 这类支持视觉输入的模型。

</details>

## 配置部分

### embedding

用于向量搜索的 Embedding 模型配置，支持 dense、sparse 和 hybrid 三种模式。

#### Dense Embedding

```json
{
  "embedding": {
    "max_concurrent": 10,
    "max_retries": 3,
    "text_source": "content_only",
    "max_input_tokens": 4096,
    "dense": {
      "provider": "volcengine",
      "api_key": "your-api-key",
      "model": "doubao-embedding-vision-251215",
      "dimension": 1024,
      "input": "multimodal",
      "batch_size": 32
    }
  }
}
```

**参数**

| 参数 | 类型 | 说明 |
|------|------|------|
| `max_concurrent` | int | 最大并发 Embedding 请求数（`embedding.max_concurrent`，默认：`10`） |
| `max_retries` | int | Embedding provider 瞬时错误的最大重试次数（`embedding.max_retries`，默认：`3`；`0` 表示禁用重试） |
| `text_source` | str | 文本文件向量化时使用的文本来源。`content_only` 读取原文内容；`summary_first` 优先使用摘要，没有摘要时回退到原文；`summary_only` 只使用摘要。默认：`content_only` |
| `max_input_tokens` | int | 使用原文内容向量化时，发送给 embedding 模型的最大估算 token 数。默认：`4096` |
| `provider` | str | `"volcengine"`、`"openai"`、`"vikingdb"`、`"jina"`、`"voyage"`、`"minimax"`、`"dashscope"` 或 `"gemini"` |
| `api_key` | str | API Key |
| `model` | str | 模型名称 |
| `dimension` | int | 向量维度 |
| `input` | str | 输入类型：`"text"` 或 `"multimodal"` |
| `batch_size` | int | 批量请求大小 |

`embedding.max_retries` 仅对瞬时错误生效，例如 `429`、`5xx`、超时和连接错误；`400`、`401`、`403`、`AccountOverdue` 这类永久错误不会自动重试。退避策略为指数退避，初始延迟 `0.5s`，上限 `8s`，并带随机抖动。

#### Embedding 熔断（Circuit Breaker）

当 embedding provider 出现连续瞬时错误（如 `429`、`5xx`）时，OpenViking 会触发熔断，在一段时间内暂停调用 provider，并将 embedding 任务重新入队。超过基础 `reset_timeout` 后进入 HALF_OPEN，允许一次探测请求；如果探测失败，则下一次 `reset_timeout` 翻倍（上限为 `max_reset_timeout`）。

```json
{
  "embedding": {
    "circuit_breaker": {
      "failure_threshold": 5,
      "reset_timeout": 60,
      "max_reset_timeout": 600
    }
  }
}
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `circuit_breaker.failure_threshold` | int | 连续失败多少次后熔断（默认：`5`） |
| `circuit_breaker.reset_timeout` | float | 基础恢复等待时间（秒，默认：`60`） |
| `circuit_breaker.max_reset_timeout` | float | 指数退避后的最大恢复等待时间（秒，默认：`600`） |

**可用模型**

| 模型 | 维度 | 输入类型 | 说明 |
|------|------|----------|------|
| `doubao-embedding-vision-251215` | 1024 | multimodal | 推荐 |
| `doubao-embedding-250615` | 1024 | text | 仅文本 |

使用 `input: "multimodal"` 时，OpenViking 可以嵌入文本、图片（PNG、JPG 等）和混合内容。

**支持的 provider:**
- `openai`: OpenAI Embedding API
- `volcengine`: 火山引擎 Embedding API
- `vikingdb`: VikingDB Embedding API
- `jina`: Jina AI Embedding API
- `voyage`: Voyage AI Embedding API
- `minimax`: MiniMax Embedding API
- `gemini`: Google Gemini Embedding API（仅文本；需安装 `google-genai>=1.0.0`）
- `dashscope`: DashScope（阿里通义）Embedding API

**minimax provider 配置示例:**

```json
{
  "embedding": {
    "dense": {
      "provider": "minimax",
      "api_key": "your-minimax-api-key",
      "model": "embo-01",
      "dimension": 1536,
      "query_param": "query",
      "document_param": "db",
      "extra_headers": {
        "GroupId": "your-group-id"
      }
    }
  }
}
```

**vikingdb provider 配置示例:**

```json
{
  "embedding": {
    "dense": {
      "provider": "vikingdb",
      "model": "bge_large_zh",
      "ak": "your-access-key",
      "sk": "your-secret-key",
      "region": "cn-beijing",
      "dimension": 1024
    }
  }
}
```

**jina provider 配置示例:**

```json
{
  "embedding": {
    "dense": {
      "provider": "jina",
      "api_key": "jina_xxx",
      "model": "jina-embeddings-v5-text-small",
      "dimension": 1024
    }
  }
}
```

可用 Jina 模型:
- `jina-embeddings-v5-text-small`: 677M 参数, 1024 维, 最大序列长度 32768 (默认)
- `jina-embeddings-v5-text-nano`: 239M 参数, 768 维, 最大序列长度 8192

**本地部署 (GGUF/MLX):** Jina 嵌入模型是开源的, 在 [Hugging Face](https://huggingface.co/jinaai) 上提供 GGUF 和 MLX 格式。可以使用任何 OpenAI 兼容的推理服务器 (如 llama.cpp、MLX、vLLM) 本地运行, 并将 `api_base` 指向本地端点:

```json
{
  "embedding": {
    "dense": {
      "provider": "jina",
      "api_key": "local",
      "api_base": "http://localhost:8080/v1",
      "model": "jina-embeddings-v5-text-nano",
      "dimension": 768
    }
  }
}
```

获取 API Key: https://jina.ai

**gemini provider 配置示例:**

> **注意：** 需安装 `pip install "google-genai>=1.0.0"`。异步批量嵌入：`pip install "openviking[gemini-async]"`。

```json
{
  "embedding": {
    "dense": {
      "provider": "gemini",
      "api_key": "your-google-api-key",
      "model": "gemini-embedding-2-preview",
      "dimension": 3072
    }
  }
}
```

可用 Gemini 嵌入模型:
- `gemini-embedding-2-preview`: 8192 token 输入限制, 1–3072 输出维度 (MRL)
- `gemini-embedding-001`: 2048 token 输入限制, 1–3072 输出维度 (MRL)
- `text-embedding-004`: 2048 token 输入限制, 768 输出维度（固定）

推荐维度: `768`、`1536` 或 `3072`（默认: `3072`）。

获取 API Key: https://aistudio.google.com/apikey

**DashScope（阿里通义）provider 配置示例:**

```json
{
  "embedding": {
    "dense": {
      "provider": "dashscope",
      "api_key": "${DASHSCOPE_API_KEY}",
      "model": "text-embedding-v4",
      "dimension": 1024
    }
  }
}
```

**可用 DashScope 模型:**

| 模型 | 维度 | 输入类型 | 说明 |
|------|------|----------|------|
| `text-embedding-v3` | 1024 | text | 针对中文优化 |
| `text-embedding-v4` | 1024 | text | 针对中文优化 |
| `tongyi-embedding-vision-plus` | 1152 | multimodal | 支持通过 `enable_fusion` 启用融合向量 |
| `tongyi-embedding-vision-flash` | 768 | multimodal | 更快，成本更低 |
| `qwen3-vl-embedding` | 2560 | multimodal | 文本 + 图像 + 视频 |
| `qwen2.5-vl-embedding` | 1024 | multimodal | 文本 + 图像 + 视频 |

**多模态参数**（仅文本+图像/视频模型支持）:

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `input_type` | str | `"multimodal"` 或 `"text"` | 嵌入模式（默认: `"multimodal"`） |
| `enable_fusion` | bool | `false` | 为 `tongyi-embedding-vision-*` 模型启用融合向量 |
| `res_level` | int | `2` | 图像分辨率级别（1=高，2=中，3=低） |
| `max_video_frames` | int | `16` | 视频最大嵌入帧数 |

**端点选择** — DashScope 为中国区（`cn`）和国际区（`intl`）提供 `api_base` 默认值:

| 区域 | `api_base` | 说明 |
|------|-----------|------|
| 中国 | `https://dashscope.aliyuncs.com`（默认） | 推荐中国大陆用户使用 |
| 国际 | `https://dashscope-intl.aliyuncs.com` | 推荐中国境外用户使用 |

也支持设置完整 URL 来自定义端点地址。

获取 API Key: https://dashscope.console.aliyun.com/api-key

**非对称检索**（索引和查询使用不同的 task type）:

```json
{
  "embedding": {
    "dense": {
      "provider": "gemini",
      "api_key": "your-google-api-key",
      "model": "gemini-embedding-2-preview",
      "dimension": 3072,
      "query_param": "RETRIEVAL_QUERY",
      "document_param": "RETRIEVAL_DOCUMENT"
    }
  }
}
```

支持的 task type: `RETRIEVAL_QUERY`、`RETRIEVAL_DOCUMENT`、`SEMANTIC_SIMILARITY`、`CLASSIFICATION`、`CLUSTERING`、`CODE_RETRIEVAL_QUERY`、`QUESTION_ANSWERING`、`FACT_VERIFICATION`。

#### Sparse Embedding

> **注意：** 火山引擎的 Sparse embedding 从 `doubao-embedding-vision-251215` 模型版本起支持。

```json
{
  "embedding": {
    "sparse": {
      "provider": "volcengine",
      "api_key": "your-api-key",
      "model": "doubao-embedding-vision-251215"
    }
  }
}
```

#### Hybrid Embedding

支持两种方式：

**方式一：使用单一混合模型**

```json
{
  "embedding": {
    "hybrid": {
      "provider": "volcengine",
      "api_key": "your-api-key",
      "model": "doubao-embedding-hybrid",
      "dimension": 1024
    }
  }
}
```

**方式二：组合 dense + sparse**

```json
{
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "api_key": "your-api-key",
      "model": "doubao-embedding-vision-251215",
      "dimension": 1024
    },
    "sparse": {
      "provider": "volcengine",
      "api_key": "your-api-key",
      "model": "doubao-embedding-vision-251215"
    }
  }
}
```

### vlm

用于语义提取（L0/L1 生成）的视觉语言模型。

```json
{
  "vlm": {
    "provider": "volcengine",
    "api_key": "your-api-key",
    "model": "doubao-seed-2-0-pro-260215",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3",
    "max_retries": 3
  }
}
```

**参数**

| 参数 | 类型 | 说明 |
|------|------|------|
| `api_key` | str | API Key |
| `model` | str | 模型名称 |
| `api_base` | str | API 端点（可选） |
| `thinking` | bool | 启用思考模式（仅对部分火山模型生效，默认：`false`） |
| `max_concurrent` | int | 语义处理阶段 LLM 最大并发调用数（默认：`100`） |
| `max_retries` | int | VLM provider 瞬时错误的最大重试次数（默认：`3`；`0` 表示禁用重试） |
| `timeout` | float | 单次 VLM API 请求的 HTTP 超时时间（秒），传递给底层 OpenAI/LiteLLM 客户端。慢端点（如 DashScope、本地推理）可调大。必须 `> 0`（默认：`60.0`） |
| `extra_headers` | object | 兼容 HTTP provider 的自定义请求头。`kimi` 默认已注入所需订阅请求头，也支持在这里覆盖或扩展 |
| `stream` | bool | 启用流式模式（OpenAI 兼容 provider 可用，默认：`false`） |

`vlm.max_retries` 仅对瞬时错误生效，例如 `429`、`5xx`、超时和连接错误；认证、鉴权、欠费等永久错误不会自动重试。退避策略为指数退避，初始延迟 `0.5s`，上限 `8s`，并带随机抖动。

**可用模型**

| 模型 | 说明 |
|------|------|
| `doubao-seed-2-0-pro-260215` | 推荐用于语义提取 |
| `doubao-pro-32k` | 用于更长上下文 |

添加资源时，VLM 生成：

1. **L0（摘要）**：~100 token 摘要
2. **L1（概览）**：~2k token 概览，包含导航信息

如果未配置 VLM，L0/L1 将直接从内容生成（语义性较弱），多模态资源的描述可能有限。

**支持的 provider：**
- `volcengine`：火山引擎 VLM API
- `openai`：OpenAI 兼容 VLM API
- `openai-codex`：通过 ChatGPT/Codex OAuth 使用 Codex VLM
- `kimi`：Kimi Coding 订阅端点，内置 provider 默认配置
- `glm`：Z.AI GLM Coding Plan 端点，使用 OpenAI 兼容请求格式

对于 `openai-codex`，请通过 `openviking-server init` 完成鉴权，再使用 `openviking-server doctor` 做校验。

**自定义 HTTP Headers**

对于 OpenAI 兼容的 provider（如 OpenRouter），可以通过 `extra_headers` 添加自定义 HTTP 请求头：

```json
{
  "vlm": {
    "provider": "openai",
    "api_key": "your-api-key",
    "model": "gpt-4o",
    "api_base": "https://openrouter.ai/api/v1",
    "extra_headers": {
      "HTTP-Referer": "https://your-site.com",
      "X-Title": "Your App Name"
    }
  }
}
```

常见使用场景：
- **OpenRouter**: 需要 `HTTP-Referer` 和 `X-Title` 来标识应用
- **Kimi Coding**: 需要自定义 user agent 或追加订阅请求头时可以在这里覆盖
- **自定义代理**: 添加认证头或追踪头
- **API 网关**: 添加版本或路由标识

**流式模式**

对于返回 SSE（Server-Sent Events）格式响应的 OpenAI 兼容 provider，启用 `stream` 模式：

```json
{
  "vlm": {
    "provider": "openai",
    "api_key": "your-api-key",
    "model": "gpt-4o",
    "api_base": "https://api.example.com/v1",
    "stream": true
  }
}
```

> **注意**: OpenAI SDK 需要 `stream=true` 才能正确解析 SSE 响应。使用强制返回 SSE 格式的 provider 时，必须将此选项设置为 `true`。

### feishu

飞书/Lark 云端文档解析配置。支持的 URL 格式详见[资源管理](../api/02-resources.md)。

```json
{
  "feishu": {
    "app_id": "",
    "app_secret": "",
    "domain": "https://open.feishu.cn",
    "max_rows_per_sheet": 1000,
    "max_records_per_table": 1000
  }
}
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `app_id` | str | 飞书应用 ID（也可通过 `FEISHU_APP_ID` 环境变量设置） |
| `app_secret` | str | 飞书应用密钥（也可通过 `FEISHU_APP_SECRET` 环境变量设置） |
| `domain` | str | 飞书 API 域名。Lark 国际版请设为 `https://open.larksuite.com` |
| `max_rows_per_sheet` | int | 电子表格每个 sheet 最大导入行数（默认 `1000`） |
| `max_records_per_table` | int | 多维表格每个表最大导入记录数（默认 `1000`） |

**依赖**：`pip install 'openviking[bot-feishu]'`

**Lark 国际版**：对于 Lark URL（`*.larksuite.com`），请将 `domain` 设为 `https://open.larksuite.com`。

### code

通过 `code_summary_mode` 控制代码文件的摘要生成方式。以下两种写法等价：

```json
{
  "code": {
    "code_summary_mode": "ast"
  }
}
```

```json
{
  "parsers": {
    "code": {
      "code_summary_mode": "ast"
    }
  }
}
```

将 `code_summary_mode` 设置为以下三个值之一：

| 值 | 说明 | 默认 |
|----|------|------|
| `"ast"` | 对 ≥100 行的代码文件提取 AST 骨架（类名、方法签名、首行注释、import），跳过 LLM 调用。**推荐用于大规模代码索引** | ✓ |
| `"llm"` | 全部走 LLM 生成摘要（成本较高） | |
| `"ast_llm"` | 先提取 AST 骨架（含完整注释），再将骨架作为上下文辅助 LLM 生成摘要（质量最高，成本居中） | |

AST 提取支持：Python、JavaScript/TypeScript、Rust、Go、Java、C/C++。其他语言、提取失败或骨架为空时自动 fallback 到 LLM。

详见 [代码骨架提取](../concepts/06-extraction.md#代码骨架提取ast-模式)。

### rerank

用于搜索结果精排的 Rerank 模型。支持 VikingDB (火山引擎)、Cohere 和 OpenAI 兼容接口。

**火山引擎 (VikingDB):**

```json
{
  "rerank": {
    "provider": "vikingdb",
    "ak": "your-access-key",
    "sk": "your-secret-key",
    "model_name": "doubao-seed-rerank",
    "model_version": "251028"
  }
}
```

**OpenAI 兼容提供方 (如 DashScope):**

```json
{
  "rerank": {
    "provider": "openai",
    "api_key": "your-api-key",
    "api_base": "https://dashscope.aliyuncs.com/compatible-api/v1/reranks",
    "model": "qwen3-vl-rerank",
    "threshold": 0.1
  }
}
```

**参数**

| 参数 | 类型 | 说明 |
|------|------|------|
| `provider` | str | `"vikingdb"`、`"cohere"` 或 `"openai"`。省略时基于字段自动识别。 |
| `ak` | str | VikingDB Access Key（仅 `vikingdb` 提供方使用） |
| `sk` | str | VikingDB Secret Key（仅 `vikingdb` 提供方使用） |
| `model_name` | str | 模型名称（仅 `vikingdb` 提供方使用，默认：`doubao-seed-rerank`） |
| `api_key` | str | API Key（用于 `openai` 或 `cohere` 提供方） |
| `api_base` | str | 接口地址（用于 `openai` 提供方） |
| `model` | str | 模型名称（用于 `openai` 提供方） |
| `threshold` | float | 分数阈值，范围为 `0.0` 到 `1.0`。低于此值的结果会被过滤。默认：`0.1` |
| `extra_headers` | object | 自定义 HTTP 请求头（OpenAI 兼容 provider 可用，可选） |

**支持的提供方:**
- `vikingdb`: 火山引擎 VikingDB Rerank API (使用 AK/SK)
- `cohere`: Cohere Rerank API
- `openai`: OpenAI 兼容的 Rerank 接口

如果未配置 Rerank，搜索仅使用向量相似度。

### retrieval

最终搜索分数的召回排序配置。

```json
{
  "retrieval": {
    "hotness_alpha": 0.0,
    "score_propagation_alpha": 0.5
  }
}
```

| 参数 | 类型 | 说明 | 默认值 |
|------|------|------|--------|
| `hotness_alpha` | float | hotness 分数在最终召回分数中的混合权重。`0.0` 表示关闭 hotness boost，最终分数等于语义相似度；`1.0` 表示只使用 hotness。有效范围：`0.0` 到 `1.0`。 | `0.0` |
| `score_propagation_alpha` | float | 层级检索中，子节点自身分数与父节点传播分数混合时，子节点自身分数的权重。`0.5` 保持当前 50/50 行为；`1.0` 表示忽略父节点分数；`0.0` 表示只使用父节点分数。有效范围：`0.0` 到 `1.0`。 | `0.5` |

如果需要分数严格反映向量相似度，保持 `hotness_alpha` 为 `0.0`。只有当希望高频访问或最近更新的上下文获得排序提升时，才将它设置为大于 `0.0`。

### storage

用于存储上下文数据 ，包括文件存储（RAGFS）和向量库存储（VectorDB）。

#### 根级配置

| 参数 | 类型 | 说明 | 默认值 |
|------|------|------|--------|
| `workspace` | str | 本地数据存储路径（主要配置） | "./data" |
| `agfs` | object | RAGFS（Rust 实现的 AGFS）配置 | {} |
| `vectordb` | object | 向量库存储配置 | {} |


```json
{
  "storage": {
    "workspace": "./data",
    "agfs": {
      "backend": "local",
      "timeout": 10
    },
    "vectordb": {
      "backend": "local"
    }
  }
}
```

#### agfs (RAGFS)

| 参数 | 类型 | 说明 | 默认值 |
|------|------|------|--------|
| `backend` | str | `"local"`、`"s3"` 或 `"memory"` | `"local"` |
| `timeout` | float | 请求超时时间（秒） | `10.0` |
| `queue_db_path` | str（可选）| 覆盖 queuefs sqlite 数据库文件路径。未设置时默认为 `{storage.workspace}/_system/queue/queue.db`。适用于 workspace 卷不支持 sqlite 的场景（例如某些网络文件系统） | `null` |
| `s3` | object | S3 backend configuration (when backend is 's3') | - |


**配置示例**

RAGFS 默认使用 Rust binding 模式，通过 Rust 实现直接访问文件系统。


##### S3 后端配置

| 参数 | 类型 | 说明 | 默认值 |
|------|------|------|--------|
| `bucket` | str | S3 存储桶名称 | null |
| `region` | str | 存储桶所在的 AWS 区域（例如 us-east-1, cn-beijing） | null |
| `access_key` | str | S3 访问密钥 ID | null |
| `secret_key` | str | 与访问密钥 ID 对应的 S3 秘密访问密钥 | null |
| `endpoint` | str | 自定义 S3 端点，对于 MinIO 或 LocalStack 等 S3 兼容服务是必需的。可以填完整 URL（`https://...` 或 `http://...`），也可以只填主机名；只填主机名时会根据 `use_ssl` 自动补 `https://` 或 `http://` | null |
| `prefix` | str | 用于命名空间隔离的可选键前缀 | "" |
| `use_ssl` | bool | 为 S3 连接启用/禁用 SSL（HTTPS）。也用于决定 `endpoint` 仅填主机名时自动补的协议前缀 | true |
| `use_path_style` | bool | true 表示对 MinIO 和某些 S3 兼容服务使用 PathStyle；false 表示对 TOS 和某些 S3 兼容服务使用 VirtualHostStyle | true |
| `directory_marker_mode` | str | 目录 marker 的持久化方式，可选 `none`、`empty`、`nonempty` | `"empty"` |
| `normalize_encoding_chars` | str | 需要在 S3 object key 中转义为 `!HH` 十六进制字节的字符集合；空字符串表示关闭编码 | `"?#%+@"` |

`directory_marker_mode` 用来控制 RAGFS 在 S3 中如何落目录对象：

- `empty` 是默认值。RAGFS 会写入 0 字节目录 marker，并保留空目录语义。
- `nonempty` 会写入非空目录 marker。对于 TOS 这类拒绝 0 字节目录 marker 的 S3 兼容后端，应使用这个模式。
- `none` 会让 RAGFS 采用更接近原生 S3 prefix 的目录语义，不再创建目录 marker 对象。此时空目录不会被持久化，只有目录下至少存在一个子对象后，相关目录才可能被发现。

典型选择：

- 对 MinIO、SeaweedFS 以及大多数 PathStyle 后端，保持默认 `empty` 即可。
- 对 TOS 或其他拒绝 0 字节目录 marker 的 VirtualHostStyle 后端，使用 `nonempty`。
- 如果你想完全使用 prefix 风格行为，并且不需要持久化空目录，可以使用 `none`。

`normalize_encoding_chars` 用来控制 RAGFS 在发起 S3 请求前需要重写哪些字符：

- 默认值是 `"?#%+@"`，所以只会转义 `?`、`#`、`%`、`+`、`@`。
- 被转义的字节会编码成 `!HH`，其中 `HH` 是该字节的大写十六进制值。
- 没有列在 `normalize_encoding_chars` 里的字符，包括中文和其他 Unicode 字符，都会保持原样。
- 设为 `""` 时，会在 object key 中保留原始路径段。

<details>
<summary><b>PathStyle S3</b></summary>
支持 PathStyle 模式的 S3 存储， 如 MinIO、SeaweedFS.

```json
{
  "storage": {
    "agfs": {
      "backend": "s3",
      "s3": {
        "bucket": "my-bucket",
        "endpoint": "s3.amazonaws.com",
        "region": "us-east-1",
        "access_key": "your-ak",
        "secret_key": "your-sk",
        "normalize_encoding_chars": "?#%+@"
      }
    }
  }
}
```
</details>


<details>
<summary><b>VirtualHostStyle S3</b></summary>
支持 VirtualHostStyle 模式的 S3 存储， 如 TOS.

```json
{
  "storage": {
    "agfs": {
      "backend": "s3",
      "s3": {
        "bucket": "my-bucket",
        "endpoint": "s3.amazonaws.com",
        "region": "us-east-1",
        "access_key": "your-ak",
        "secret_key": "your-sk",
        "use_path_style": false,
        "directory_marker_mode": "nonempty",
        "normalize_encoding_chars": "?#%+@"
      }
    }
  }
}
```

</details>

#### vectordb

向量库存储的配置

| 参数 | 类型 | 说明 | 默认值 |
|------|------|------|--------|
| `backend` | str | VectorDB 后端类型: 'local'（基于文件）, 'http'（远程服务）, 'volcengine'（云上VikingDB）或 'vikingdb'（私有部署） | "local" |
| `name` | str | VectorDB 的集合名称 | "context" |
| `url` | str | 'http' 类型的远程服务 URL（例如 'http://localhost:5000'） | null |
| `project_name` | str | 项目名称（别名 project） | "default" |
| `distance_metric` | str | 向量相似度搜索的距离度量（例如 'cosine', 'l2', 'ip'） | "cosine" |
| `dimension` | int | 向量嵌入的维度 | 0 |
| `sparse_weight` | float | 混合向量搜索的稀疏权重，仅在使用混合索引时生效 | 0.0 |
| `volcengine` | object | 'volcengine' 类型的 VikingDB 配置 | - |
| `vikingdb` | object | 'vikingdb' 类型的私有部署配置 | - |

默认使用本地模式
```
{
  "storage": {
    "vectordb": {
      "backend": "local"
    }
  }
}
```

<details>
<summary><b>volcengine vikingDB</b></summary>
支持火山引擎云上部署的 VikingDB

```json
{
  "storage": {
    "vectordb": {
      "name": "context",
      "backend": "volcengine",
      "project": "default",
      "volcengine": {
        "region": "cn-beijing",
        "ak": "your-access-key",
        "sk": "your-secret-key"
      }
  }
}
```
</details>



## 配置文件

OpenViking 使用两个配置文件：

| 配置文件 | 用途 | 默认路径 |
|---------|------|---------|
| `ov.conf` | SDK 嵌入模式 + 服务端配置 | `~/.openviking/ov.conf` |
| `ovcli.conf` | HTTP 客户端和 CLI 连接远程服务端 | `~/.openviking/ovcli.conf` |

配置文件放在默认路径时，OpenViking 自动加载，无需额外设置。

如果配置文件在其他位置，有两种指定方式：

```bash
# 方式一：环境变量
export OPENVIKING_CONFIG_FILE=/path/to/ov.conf
export OPENVIKING_CLI_CONFIG_FILE=/path/to/ovcli.conf

# 方式二：命令行参数（仅 serve 命令）
openviking-server --config /path/to/ov.conf
```

### ov.conf

本文档上方各配置段（embedding、vlm、rerank、storage）均属于 `ov.conf`。SDK 嵌入模式和服务端共用此文件。

如需配置 memory 相关行为，可在 `ov.conf` 中添加 `memory` 段：

```json
{
  "memory": {
    "agent_scope_mode": "user+agent"
  }
}
```

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `agent_scope_mode` | 已废弃且被忽略。仅为兼容旧版 `ov.conf` 保留。当前 agent/user 命名空间行为由 account 级 namespace policy 控制。 | `"user+agent"` |

`agent_scope_mode` 不再影响命名空间行为。服务端现在根据 account 级 namespace policy 在 `viking://agent/{agent_id}/...` 与 `viking://agent/{agent_id}/user/{user_id}/...` 之间选择。

### ovcli.conf

HTTP 客户端（`SyncHTTPClient` / `AsyncHTTPClient`）和 CLI 工具连接远程服务端的配置文件：

```json
{
  "url": "http://localhost:1933",
  "api_key": "your-secret-key",
  "account": "acme",
  "user": "alice",
  "agent_id": "my-agent",
  "upload": {
    "ignore_dirs": "node_modules,.cache,.nx",
    "include": "*.md,*.pdf",
    "exclude": "*.tmp,*.log"
  }
}
```

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `url` | 服务端地址 | （必填） |
| `api_key` | API Key 认证（root key 或 user key） | `null`（无认证） |
| `account` | 默认发送为 `X-OpenViking-Account` 的租户标识 | `null` |
| `user` | 默认发送为 `X-OpenViking-User` 的用户标识 | `null` |
| `agent_id` | Agent 标识，用于 agent space 隔离 | `null` |
| `upload.ignore_dirs` | `add-resource` 默认忽略目录列表（CSV） | `null` |
| `upload.include` | `add-resource` 默认包含模式（CSV） | `null` |
| `upload.exclude` | `add-resource` 默认排除模式（CSV） | `null` |

本地目录上传会默认遵循 `.gitignore`（根目录和子目录，含 `!` 反向规则）。`ignore_dirs/include/exclude` 会在此基础上进一步过滤。

也可以在单次命令里用 CLI 参数覆盖这些身份字段：

```bash
openviking --account acme --user alice --agent-id assistant-2 ls viking://
```

对于 `add-resource`，上传过滤参数会与 `ovcli.conf` 默认值做合并（追加），不会覆盖：

```bash
# ovcli.conf: upload.exclude="*.log"
openviking add-resource ./docs --exclude "*.tmp"
# 实际发送给服务端的 exclude: "*.log,*.tmp"
```

详见 [服务部署](./03-deployment.md)。

## server 段

将 OpenViking 作为 HTTP 服务运行时，在 `ov.conf` 中添加 `server` 段：

```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 1933,
    "auth_mode": "api_key",
    "root_api_key": "your-secret-root-key",
    "cors_origins": ["*"]
  }
}
```

| 字段 | 类型 | 说明 | 默认值 |
|------|------|------|--------|
| `host` | str | 绑定地址 | `127.0.0.1` |
| `port` | int | 绑定端口 | `1933` |
| `auth_mode` | str | 认证模式：`"api_key"` 或 `"trusted"`。默认值为 `"api_key"` | `"api_key"` |
| `root_api_key` | str | Root API Key。在 `api_key` 模式下启用多租户认证；在 `trusted` 模式下它只是可选附加保护，不负责解析普通用户身份 | `null` |
| `cors_origins` | list | CORS 允许的来源 | `["*"]` |

`api_key` 模式使用 API Key 认证，也是默认模式；`trusted` 模式信任上游网关或受信调用方注入的 `X-OpenViking-Account` / `X-OpenViking-User` 请求头。

在 `api_key` 模式下配置 `root_api_key` 后，服务端启用正式多租户认证，并通过 Admin API 创建工作区和用户 key。在 `trusted` 模式下，普通请求不需要先注册 user key；每个请求都会根据注入的身份头解析成 `USER`。只有在 `auth_mode = "api_key"` 且未配置 `root_api_key` 时，服务端才会进入开发模式。

启动方式和部署详情见 [服务部署](./03-deployment.md)，认证详情见 [认证](./04-authentication.md)。

## encryption 段

启用静态数据加密，确保多租户环境下的数据安全与隔离。加密功能对用户完全透明，API 无变化。

```json
{
  "encryption": {
    "enabled": true,
    "provider": "local|vault|volcengine_kms"
  }
}
```

| 参数 | 类型 | 说明 | 默认值 |
|------|------|------|--------|
| `enabled` | bool | 是否启用加密 | `false` |
| `provider` | str | 密钥提供程序：`"local"`、`"vault"` 或 `"volcengine_kms"` | - |
| `api_key_hashing.enabled` | bool | 是否对 API key 字段启用 Argon2id 单向哈希（与文件级 `enabled` 独立控制），详见 [加密指南](./08-encryption.md) | `false` |

### Local（本地文件）

适合开发环境和单节点部署：

```json
{
  "encryption": {
    "enabled": true,
    "provider": "local",
    "local": {
      "key_file": "~/.openviking/master.key"
    }
  }
}
```

| 参数 | 类型 | 说明 | 默认值 |
|------|------|------|--------|
| `local.key_file` | str | 根密钥文件路径 | `~/.openviking/master.key` |

### Vault（HashiCorp Vault）

适合生产环境和多云部署：

```json
{
  "encryption": {
    "enabled": true,
    "provider": "vault",
    "vault": {
      "address": "https://vault.example.com:8200",
      "token": "vault-token-xxx",
      "mount_point": "transit",
      "key_name": "openviking-root"
    }
  }
}
```

| 参数 | 类型 | 说明 | 默认值 |
|------|------|------|--------|
| `vault.address` | str | Vault 服务地址 | - |
| `vault.token` | str | Vault 访问令牌 | - |
| `vault.mount_point` | str | Transit 引擎挂载点 | `"transit"` |
| `vault.key_name` | str | 根密钥名称 | `"openviking-root"` |

### Volcengine KMS（火山引擎）

适合火山引擎云部署：

```json
{
  "encryption": {
    "enabled": true,
    "provider": "volcengine_kms",
    "volcengine_kms": {
      "key_id": "kms-key-id-xxx",
      "region": "cn-beijing",
      "access_key": "AKLTxxxxxxxx",
      "secret_key": "Tmpxxxxxxxx"
    }
  }
}
```

| 参数 | 类型 | 说明 | 默认值 |
|------|------|------|--------|
| `volcengine_kms.key_id` | str | KMS 密钥 ID | - |
| `volcengine_kms.region` | str | 区域 | `"cn-beijing"` |
| `volcengine_kms.access_key` | str | 火山引擎 Access Key | - |
| `volcengine_kms.secret_key` | str | 火山引擎 Secret Key | - |

加密功能的详细说明见 [数据加密](../concepts/10-encryption.md)，完整使用流程见 [加密指南](./08-encryption.md)。

## storage.transaction 段

路径锁默认启用，通常无需配置。**默认行为是不等待**：若目标路径已被其他操作锁定，操作立即失败并抛出 `LockAcquisitionError`。若需要等待重试，请将 `lock_timeout` 设为正数。

```json
{
  "storage": {
    "transaction": {
      "lock_timeout": 5.0,
      "lock_expire": 300.0
    }
  }
}
```

| 参数 | 类型 | 说明 | 默认值 |
|------|------|------|--------|
| `lock_timeout` | float | 获取路径锁的等待超时（秒）。`0` = 立即失败（默认）；`> 0` = 最多等待此时间后抛出 `LockAcquisitionError` | `0.0` |
| `lock_expire` | float | 锁失活阈值（秒）。超过此时间未被 refresh 的锁会被视为陈旧锁并回收 | `300.0` |

路径锁机制的详细说明见 [路径锁与崩溃恢复](../concepts/09-transaction.md)。

## 完整 Schema

```json
{
  "embedding": {
    "max_concurrent": 10,
    "max_retries": 3,
    "text_source": "content_only",
    "max_input_tokens": 4096,
    "dense": {
      "provider": "volcengine",
      "api_key": "string",
      "model": "string",
      "dimension": 1024,
      "input": "multimodal"
    }
  },
  "vlm": {
    "provider": "string",
    "api_key": "string",
    "model": "string",
    "api_base": "string",
    "thinking": false,
    "max_concurrent": 100,
    "max_retries": 3,
    "extra_headers": {},
    "stream": false
  },
  "rerank": {
    "provider": "volcengine|openai",
    "api_key": "string",
    "model": "string",
    "api_base": "string",
    "threshold": 0.1,
    "extra_headers": {}
  },
  "retrieval": {
    "hotness_alpha": 0.0,
    "score_propagation_alpha": 0.5
  },
  "encryption": {
    "enabled": false,
    "provider": "local|vault|volcengine_kms",
    "local": {
      "key_file": "~/.openviking/master.key"
    },
    "vault": {
      "address": "https://vault.example.com:8200",
      "token": "string",
      "mount_point": "transit",
      "key_name": "openviking-root"
    },
    "volcengine_kms": {
      "key_id": "string",
      "region": "cn-beijing",
      "access_key": "string",
      "secret_key": "string"
    }
  },
  "storage": {
    "workspace": "string",
    "agfs": {
      "backend": "local|s3|memory",
      "timeout": 10
    },
    "transaction": {
      "lock_timeout": 0.0,
      "lock_expire": 300.0
    },
    "vectordb": {
      "backend": "local|remote",
      "url": "string",
      "project": "string"
    }
  },
  "code": {
    "code_summary_mode": "ast"
  },
  "server": {
    "host": "string",
    "port": 1933,
    "root_api_key": "string",
    "cors_origins": ["string"]
  }
}
```

说明：
- `storage.vectordb.sparse_weight` 用于混合（dense + sparse）索引/检索的权重，仅在使用 hybrid 索引时生效；设置为 > 0 才会启用 sparse 信号。

## 故障排除

### API Key 错误

```
Error: Invalid API key
```

检查 API Key 是否正确且有相应权限。

### 维度不匹配

```
Error: Vector dimension mismatch
```

确保配置中的 `dimension` 与模型输出维度匹配。

### VLM 超时

```
Error: VLM request timeout
```

- 检查网络连接
- 增加配置中的超时时间
- 对偶发超时，适当增大 `vlm.max_retries`
- 尝试更小的模型
- 如为批量导入场景，结合降低 `vlm.max_concurrent`

### 速率限制

```
Error: Rate limit exceeded
```

火山引擎有速率限制。考虑批量处理时添加延迟或升级套餐。
- 优先降低 `embedding.max_concurrent` / `vlm.max_concurrent`
- 对偶发 `429` 可保留少量 `max_retries`；若希望快速失败，可将其设为 `0`

## 相关文档

- [火山引擎购买指南](./02-volcengine-purchase-guide.md) - API Key 获取
- [API 概览](../api/01-overview.md) - 客户端初始化
- [服务部署](./03-deployment.md) - Server 配置
- [上下文层级](../concepts/03-context-layers.md) - L0/L1/L2
