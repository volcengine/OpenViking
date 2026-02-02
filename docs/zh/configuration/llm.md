# LLM 配置

配置用于语义提取（L0/L1 生成）和重排序的 LLM。

## VLM（视觉语言模型）

用于从资源生成 L0/L1 内容。

```json
{
  "vlm": {
    "provider": "volcengine",
    "api_key": "your-volcengine-api-key",
    "model": "doubao-seed-1-8-251228",
    "base_url": "https://ark.cn-beijing.volces.com/api/v3"
  }
}
```

### 参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `api_key` | str | 火山引擎 API Key |
| `model` | str | 模型名称 |
| `base_url` | str | API 端点（可选） |

### 可用模型

| 模型 | 说明 |
|------|------|
| `doubao-seed-1-8-251228` | 推荐用于语义提取 |
| `doubao-pro-32k` | 用于更长上下文 |

## Rerank 模型

用于搜索结果精排。

```json
{
  "rerank": {
    "provider": "volcengine",
    "api_key": "your-volcengine-api-key",
    "model": "doubao-rerank-250615"
  }
}
```

### 参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `provider` | str | `"volcengine"` |
| `api_key` | str | 火山引擎 API Key |
| `model` | str | 模型名称 |

## 环境变量

```bash
export VOLCENGINE_API_KEY="your-api-key"
```

## 编程式配置

```python
from openviking.utils.config import OpenVikingConfig

config = OpenVikingConfig(
    vlm={
        "api_key": "your-api-key",
        "model": "doubao-seed-1-8-251228"
    },
    rerank={
        "provider": "volcengine",
        "api_key": "your-api-key",
        "model": "doubao-rerank-250615"
    }
)
```

## LLM 的使用方式

### L0/L1 生成

添加资源时，VLM 生成：

1. **L0（摘要）**：~100 token 摘要
2. **L1（概览）**：~2k token 概览，包含导航信息

```
资源 → Parser → VLM → L0/L1 → 存储
```

### 重排序

搜索时，Rerank 模型精排结果：

```
查询 → 向量搜索 → 候选 → Rerank → 最终结果
```

## 禁用 LLM 功能

### 不配置 VLM

如果未配置 VLM：
- L0/L1 将直接从内容生成（语义性较弱）
- 多模态资源的描述可能有限

### 不配置 Rerank

如果未配置 Rerank：
- 搜索仅使用向量相似度
- 结果可能不够准确

## 故障排除

### VLM 超时

```
Error: VLM request timeout
```

- 检查网络连接
- 增加配置中的超时时间
- 尝试更小的模型

### Rerank 不工作

```
Warning: Rerank not configured, using vector search only
```

添加 Rerank 配置以启用两阶段检索。

## 相关文档

- [配置](./configuration.md) - 主配置
- [Embedding 配置](./embedding.md) - Embedding 设置
- [上下文层级](../concepts/context-layers.md) - L0/L1/L2
