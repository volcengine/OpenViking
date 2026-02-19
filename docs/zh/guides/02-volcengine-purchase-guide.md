# 火山引擎模型购买指南

本指南介绍如何在火山引擎购买和配置 OpenViking 所需的模型服务。

## 概述

OpenViking 需要以下模型服务：

| 模型类型 | 用途 | 推荐模型 |
|---------|------|---------|
| VLM（视觉语言模型） | 内容理解、语义生成 | `doubao-seed-1-8-251228` |
| Embedding | 向量化、语义检索 | `doubao-embedding-vision-250615` |

## 前置条件

- 有效的手机号或邮箱
- 完成实名认证（个人或企业）

## 购买流程

### 1. 注册账号

访问 [火山引擎官网](https://www.volcengine.com/)：

1. 点击右上角"登录/注册"
2. 选择注册方式（手机号/邮箱）
3. 完成验证并设置密码
4. 进行实名认证


### 2. 开通火山方舟

火山方舟是火山引擎的 AI 模型服务平台。

#### 访问控制台

1. 登录后进入[控制台](https://console.volcengine.com/)
2. 搜索"火山方舟"
3. 点击进入[火山方舟控制台](https://console.volcengine.com/ark/region:ark+cn-beijing/model)
4. 首次使用需要点击"开通服务"并同意协议

### 3. 创建 API Key

访问：[API Key 管理页面](https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey)

所有模型调用都需要 API Key。

1. 在火山方舟左侧导航栏选择 **"API Key 管理"**
2. 点击 **"创建 API Key"**
3. 复制保存API Key以用于后续配置

<div align="center">
<img src="../../images/create_api_key.gif" width="80%">
</div>


### 4. 开通 VLM 模型

访问：[模型管理页面](https://console.volcengine.com/ark/region:ark+cn-beijing/model)

1. 在左侧导航栏选择 **"开通管理"**
2. 选择 **"语言模型"** 一列
3. 找到 **Doubao-Seed-1.8** 模型
4. 点击"开通"按钮
5. 确认付费方式

<div align="center">
<img src="../../images/activate_vlm_model.gif" width="80%">
</div>

开通后可直接使用模型 ID：`doubao-seed-1-8-251228`

### 5. 开通 Embedding 模型

访问：[模型管理页面](https://console.volcengine.com/ark/region:ark+cn-beijing/model)

1. 在左侧导航栏选择 **"开通管理"** 
2. 选择 **"向量模型"** 一列
3. 找到 **Doubao-Embedding-Vision** 模型
4. 点击"开通"
5. 确认付费方式

<div align="center">
<img src="../../images/activate_emb_model.gif" width="80%">
</div>

开通后使用模型 ID：`doubao-embedding-vision-250615`

## 配置 OpenViking

### 配置模板

创建 `~/.openviking/ov.conf` 文件，使用以下模板：

```json
{
  "vlm": {
    "provider": "<provider-type>",
    "api_key": "<your-api-key>",
    "model": "<model-id>",
    "api_base": "<api-endpoint>",
    "temperature": <temperature-value>,
    "max_retries": <retry-count>
  },
  "embedding": {
    "dense": {
      "provider": "<provider-type>",
      "api_key": "<your-api-key>",
      "model": "<model-id>",
      "api_base": "<api-endpoint>",
      "dimension": <vector-dimension>,
      "input": "<input-type>"
    }
  }
}
```

### 配置字段说明

#### VLM 配置字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `provider` | string | 是 | 模型服务提供商，火山引擎填 `"volcengine"` |
| `api_key` | string | 是 | 火山方舟 API Key |
| `model` | string | 是 | 模型 ID，如 `doubao-seed-1-8-251228` |
| `api_base` | string | 否 | API 端点地址，默认为北京区域端点，具体可见附录-区域端点 |
| `temperature` | float | 否 | 生成温度，控制输出随机性，范围 0-1，推荐 0.1 |
| `max_retries` | int | 否 | 请求失败时的重试次数，推荐 3 |

#### Embedding 配置字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `provider` | string | 是 | 模型服务提供商，火山引擎填 `"volcengine"` |
| `api_key` | string | 是 | 火山方舟 API Key |
| `model` | string | 是 | 模型 ID，如 `doubao-embedding-vision-250615` |
| `api_base` | string | 否 | API 端点地址，默认为北京区域端点，具体可见附录-区域端点 |
| `dimension` | int | 是 | 向量维度，取决于模型（通常为 1024 或 768） |
| `input` | string | 否 | 输入类型：`"multimodal"`（多模态）或 `"text"`（纯文本），默认`"multimodal"` |

### 配置示例

将以下内容保存为 `~/.openviking/ov.conf`：

```json
{
  "vlm": {
    "provider": "volcengine",
    "api_key": "sk-1234567890abcdef1234567890abcdef",
    "model": "doubao-seed-1-8-251228",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3",
    "temperature": 0.1,
    "max_retries": 3
  },
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "api_key": "sk-1234567890abcdef1234567890abcdef",
      "model": "doubao-embedding-vision-250615",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3",
      "dimension": 1024,
      "input": "multimodal"
    }
  }
}
```

> ⚠️ **注意**：请将示例中的 `api_key` 替换为你在第 3 步获取的真实 API Key！

## 验证配置

### 测试连接

```python
import openviking as ov
import asyncio

async def test():
    client = ov.AsyncOpenViking(path="./test_data")
    await client.initialize()

    # 添加简单资源测试
    result = await client.add_resource(
        "https://example.com",
        reason="测试连接"
    )
    print(f"✓ 配置成功: {result['root_uri']}")

    await client.close()

asyncio.run(test())
```

### 查看使用情况

在火山方舟控制台：

1. 访问 **"概览"** 页面
2. 查看 **Token 消耗统计**
3. 在 **"费用中心"** 查看账单明细

## 费用说明

### 计费方式

| 模型类型 | 计费单位 |
|---------|---------|
| VLM | 按输入/输出 Token 计费 |
| Embedding | 按文本长度计费 |

### 免费额度

火山引擎为新用户提供免费额度：

- 首次开通赠送 Token
- 足够完成 OpenViking 的试用体验
- 详见：[火山方舟定价说明](https://www.volcengine.com/docs/82379/1399514)

## 故障排除

### 常见错误

#### API Key 无效

```
Error: Invalid API Key
```

**解决方法**：
1. 检查 API Key 是否正确复制（完整的 `sk-` 开头字符串）
2. 确认 API Key 未被删除或过期
3. 重新创建 API Key

#### 模型未开通

```
Error: Model not activated
```

**解决方法**：
1. 在火山方舟控制台检查模型状态
2. 确认模型处于"运行中"状态
3. 检查账户余额是否充足

#### 网络连接问题

```
Error: Connection timeout
```

**解决方法**：
1. 检查网络连接
2. 确认 `api_base` 配置正确
3. 如在海外，确认可访问火山引擎服务
4. 增加配置中的超时时间

### 获取帮助

- [火山引擎文档中心](https://www.volcengine.com/docs)
- [火山方舟 API 文档](https://www.volcengine.com/docs/82379)
- [OpenViking GitHub Issues](https://github.com/volcengine/OpenViking/issues)

## 相关文档

- [配置指南](./01-configuration.md) - 完整配置参考
- [快速开始](../getting-started/02-quickstart.md) - 开始使用 OpenViking

## 附录

### 区域端点

| 区域 | API Base |
|------|----------|
| 北京 | `https://ark.cn-beijing.volces.com/api/v3` |
| 上海 | `https://ark.cn-shanghai.volces.com/api/v3` |

### 模型版本对照

| 模型名称 | 当前版本 | 发布日期 |
|---------|---------|---------|
| Doubao-Seed-1.8 | `doubao-seed-1-8-251228` | 2025-12-28 |
| Doubao-Embedding-Vision | `doubao-embedding-vision-250615` | 2025-06-15 |

> 注：模型版本可能更新，请以火山方舟控制台显示为准。
