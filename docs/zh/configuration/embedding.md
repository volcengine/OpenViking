# Embedding 配置

配置用于向量搜索的 Embedding 模型。

## 火山引擎 Doubao（推荐）

```json
{
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "api_key": "your-volcengine-api-key",
      "model": "doubao-embedding-vision-250615",
      "dimension": 1024,
      "input": "multimodal"
    }
  }
}
```

### 参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `provider` | str | `"volcengine"` |
| `api_key` | str | 火山引擎 API Key |
| `model` | str | 模型名称 |
| `dimension` | int | 向量维度 |
| `input` | str | 输入类型：`"text"` 或 `"multimodal"` |

### 可用模型

| 模型 | 维度 | 输入类型 | 说明 |
|------|------|----------|------|
| `doubao-embedding-vision-250615` | 1024 | multimodal | 推荐 |
| `doubao-embedding-250615` | 1024 | text | 仅文本 |

## 获取火山引擎 API Key

1. 访问 [火山引擎控制台](https://console.volcengine.com/)
2. 进入 **方舟** 服务
3. 创建 API Key
4. 复制 Key 到配置文件

## 环境变量

```bash
export VOLCENGINE_API_KEY="your-api-key"
```

然后在配置中：

```json
{
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "model": "doubao-embedding-vision-250615",
      "dimension": 1024
    }
  }
}
```

## 编程式配置

```python
from openviking.utils.config import EmbeddingConfig, DenseEmbeddingConfig

embedding_config = EmbeddingConfig(
    dense=DenseEmbeddingConfig(
        provider="volcengine",
        api_key="your-api-key",
        model="doubao-embedding-vision-250615",
        dimension=1024,
        input="multimodal"
    )
)
```

## 多模态支持

使用 `input: "multimodal"` 时，OpenViking 可以嵌入：

- 文本内容
- 图片（PNG、JPG 等）
- 混合文本和图片

```python
# 自动使用多模态嵌入
await client.add_resource("image.png")  # 图片嵌入
await client.add_resource("doc.pdf")    # 文本 + 图片嵌入
```

## 故障排除

### API Key 错误

```
Error: Invalid API key
```

检查 API Key 是否正确且有 Embedding 权限。

### 维度不匹配

```
Error: Vector dimension mismatch
```

确保配置中的 `dimension` 与模型输出维度匹配。

### 速率限制

```
Error: Rate limit exceeded
```

火山引擎有速率限制。考虑：
- 批量处理时添加延迟
- 升级套餐

## 相关文档

- [配置](./configuration.md) - 主配置
- [LLM 配置](./llm.md) - LLM 设置
- [资源管理](../api/resources.md) - 添加资源
