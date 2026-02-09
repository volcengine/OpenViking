# 快速开始

5 分钟上手 OpenViking。

## 前置要求

在开始使用 OpenViking 之前，请确保您的环境满足以下要求：

- **Python 版本**：3.9 或更高版本
- **操作系统**：Linux、macOS、Windows
- **网络连接**：需要稳定的网络连接（用于下载依赖包和访问模型服务）

## 安装

```bash
pip install openviking
```

## 模型准备

OpenViking 需要以下模型能力：
- **VLM 模型**：用于图像和内容理解
- **Embedding 模型**：用于向量化和语义检索

OpenViking 支持多种模型服务：
- **火山引擎（豆包模型）**：推荐使用，成本低、性能好，新用户有免费额度。如需购买和开通，请参考：[火山引擎购买指南](../guides/02-volcengine-purchase-guide.md)
- **OpenAI 模型**：支持 GPT-4V 等 VLM 模型和 OpenAI Embedding 模型
- **其他自定义模型服务**：支持兼容 OpenAI API 格式的模型服务

## 配置环境

### 配置文件模版

创建配置文件 `ov.conf`：

```json
{
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

### 配置示例

<details>
<summary><b>示例 1：使用火山引擎（豆包模型）</b></summary>

```json
{
  "embedding": {
    "dense": {
      "api_base" : "https://ark.cn-beijing.volces.com/api/v3",
      "api_key"  : "your-volcengine-api-key",
      "provider" : "volcengine",
      "dimension": 1024,
      "model"    : "doubao-embedding-vision-250615"
    }
  },
  "vlm": {
    "api_base" : "https://ark.cn-beijing.volces.com/api/v3",
    "api_key"  : "your-volcengine-api-key",
    "provider" : "volcengine",
    "model"    : "doubao-seed-1-8-251228"
  }
}
```

</details>

<details>
<summary><b>示例 2：使用 OpenAI 模型</b></summary>

```json
{
  "embedding": {
    "dense": {
      "api_base" : "https://api.openai.com/v1",
      "api_key"  : "your-openai-api-key",
      "provider" : "openai",
      "dimension": 3072,
      "model"    : "text-embedding-3-large"
    }
  },
  "vlm": {
    "api_base" : "https://api.openai.com/v1",
    "api_key"  : "your-openai-api-key",
    "provider" : "openai",
    "model"    : "gpt-4-vision-preview"
  }
}
```

</details>

### 设置环境变量

创建好配置文件后，设置环境变量指向配置文件：

```bash
export OPENVIKING_CONFIG_FILE=ov.conf
```

## 运行第一个示例

### 创建 Python 脚本

创建 `example.py`：

```python
import openviking as ov

# Initialize OpenViking client with data directory
client = ov.OpenViking(path="./data")

try:
    # Initialize the client
    client.initialize()

    # Add resource (supports URL, file, or directory)
    add_result = client.add_resource(
        path="https://raw.githubusercontent.com/volcengine/OpenViking/refs/heads/main/README.md"
    )
    root_uri = add_result['root_uri']

    # Explore the resource tree structure
    ls_result = client.ls(root_uri)
    print(f"Directory structure:\n{ls_result}\n")

    # Use glob to find markdown files
    glob_result = client.glob(pattern="**/*.md", uri=root_uri)
    if glob_result['matches']:
        content = client.read(glob_result['matches'][0])
        print(f"Content preview: {content[:200]}...\n")

    # Wait for semantic processing to complete
    print("Wait for semantic processing...")
    client.wait_processed()

    # Get abstract and overview of the resource
    abstract = client.abstract(root_uri)
    overview = client.overview(root_uri)
    print(f"Abstract:\n{abstract}\n\nOverview:\n{overview}\n")

    # Perform semantic search
    results = client.find("what is openviking", target_uri=root_uri)
    print("Search results:")
    for r in results.resources:
        print(f"  {r.uri} (score: {r.score:.4f})")

    # Close the client
    client.close()

except Exception as e:
    print(f"Error: {e}")
```

### 运行脚本

```bash
python example.py
```

### 预期输出

```
Directory structure:
...

Content preview: ...

Wait for semantic processing...
Abstract:
...

Overview:
...

Search results:
  viking://resources/... (score: 0.8523)
  ...
```

恭喜！你已成功运行 OpenViking。

## 服务端模式

想要将 OpenViking 作为共享服务运行？请参见 [快速开始：服务端模式](03-quickstart-server.md)。

## 下一步

- [配置详解](../guides/01-configuration.md) - 详细配置选项
- [API 概览](../api/01-overview.md) - API 参考
- [资源管理](../api/02-resources.md) - 资源管理 API
