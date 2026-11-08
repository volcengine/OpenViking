# 火山引擎模型购买指南

本指南适用于自建 OpenViking：开通火山方舟模型、取得 API Key，并验证服务端配置。使用托管 OpenViking 时，无需在客户端配置这些模型，见[快速开始](../getting-started/02-quickstart.md)。

## 概述

OpenViking 需要以下模型服务：

| 模型类型 | 用途 | 推荐模型 |
|---------|------|---------|
| VLM（视觉语言模型） | 内容理解、语义生成 | `doubao-seed-2-0-lite-260428` |
| Embedding | 向量化、语义检索 | `doubao-embedding-vision-251215` |

## 前置条件

- 有效的手机号或邮箱
- 完成实名认证（个人或企业）

## 通过火山账号授权配置

自建服务可以通过浏览器授权，同时配置 VLM 和 Embedding，无需安装 ArkCLI 或手动复制 API Key：

```bash
openviking-server init
```

在交互式终端中选择 **VolcEngine account setup (火山账号授权配置)**。先选择 **Agent Plan、Coding Plan 或按量付费**。向导再打开火山官方授权页面；确认设备码并授权后，OpenViking 自动获取 `default` 项目下与所选接入方式匹配的 API Key。仅有一把时自动使用，有多把时按名称选择；获取失败时可在同一授权会话中重试或输入其他项目名称。列表为空时也可切换项目、打开方舟控制台创建 Key，或返回安装菜单；创建后返回向导自动获取，无需复制密钥。浏览器无法自动打开时，可手动访问打印的链接，并在当前授权会话中继续。

授权后分别选择 VLM 和 Embedding，无需手填模型名。Agent Plan 根据当前有效个人订阅的档位，通过控制面 `ListAgentPlanVirtualModelID` 获取可直接调用的模型名，并依据返回的输入/输出模态分类；不请求 `/api/plan/v3/models`。其他接入方式使用所选接口的 `/models` 目录。VLM 菜单仅展示已分类为支持视觉输入的模型。默认嵌入模型自动填写维度和多模态协议；其他嵌入模型优先使用目录中的输入能力，缺少信息时需根据模型文档确认文本或多模态接口协议及维度。验证请求与保存配置使用相同协议。目录可见不代表已开通，仍需通过实际调用验证。选择验证时，会分别发送一次小型模型请求，按方舟规则计费。失败时可修正模型后重试，或返回安装菜单；也可在发起验证前选择跳过，并在之后自行检查可用性。

Agent Plan 需有有效个人订阅且已完成所需协议确认；无法确定订阅档位或获取套餐目录时，向导会提示重试，不会切换到按量付费目录。按量付费会额外查询账号开通状态；缺少能力信息的模型暂不展示。模型调用成功不代表已核验套餐抵扣。

向导会同时匹配凭据来源、接口地址和模型名：

| 接入方式 | 凭据来源 | API 路径 | 默认嵌入模型 |
| --- | --- | --- | --- |
| Agent Plan 个人版 | 个人套餐专属 Key | `/api/plan/v3` | `doubao-embedding-vision` |
| Coding Plan 个人版 | 普通方舟 Key | `/api/coding/v3` | `doubao-embedding-vision` |
| 按量付费 | 普通方舟 Key | `/api/v3` | `doubao-embedding-vision-251215` |

自动授权入口目前使用北京地域。企业版席位、其他地域可使用逐步配置入口手动填写凭据。实名认证、协议确认、套餐权益、模型开通、余额和项目权限仍需满足方舟要求。

授权使用火山的公开设备码协议，临时 OAuth / STS 凭据只保留在当前向导进程内，不读取或修改 ArkCLI、ve CLI 的配置和登录态。保存前会再次确认，选定的模型 API Key 随配置写入权限为 `0600` 的 `ov.conf`，不会在摘要中显示。

测试时可以指定独立配置文件，数据目录会位于该文件旁的 `data` 目录，不影响默认配置：

```bash
openviking-server init --config /tmp/openviking-volcengine-test/ov.conf
```

已有配置可选择 **Start over** 后进入授权配置；保存时原配置会备份。若只想修改单个模型，请使用 **Update VLM** 或 **Update embedding**。

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
3. 找到 **Doubao-Seed-2.0** 模型
4. 点击"开通"按钮
5. 确认付费方式

<div align="center">
<img src="../../images/activate_vlm_model.gif" width="80%">
</div>

开通后可直接使用模型 ID：`doubao-seed-2-0-lite-260428`

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

开通后使用模型 ID：`doubao-embedding-vision-251215`

## 配置 OpenViking

### 配置模板

以下模板说明配置结构；替换所有字符串占位值后再使用。已有配置时只更新相关段落，保留存储、认证等其他配置：

```json
{
  "vlm": {
    "provider": "<provider-type>",
    "api_key": "<your-api-key>",
    "model": "<model-id>",
    "api_base": "<api-endpoint>",
    "temperature": 0.1,
    "max_retries": 3
  },
  "embedding": {
    "dense": {
      "provider": "<provider-type>",
      "api_key": "<your-api-key>",
      "model": "<model-id>",
      "api_base": "<api-endpoint>",
      "dimension": 1024,
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
| `model` | string | 是 | 模型 ID，如 `doubao-seed-2-0-lite-260428` |
| `api_base` | string | 否 | API 端点地址，默认为北京区域端点，具体可见附录-区域端点 |
| `temperature` | float | 否 | 生成温度；支持范围取决于模型，本例使用 0.1 |
| `max_retries` | int | 否 | 请求失败时的重试次数，推荐 3 |

#### Embedding 配置字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `provider` | string | 是 | 模型服务提供商，火山引擎填 `"volcengine"` |
| `api_key` | string | 是 | 火山方舟 API Key |
| `model` | string | 是 | 模型 ID，如 `doubao-embedding-vision-251215` |
| `api_base` | string | 否 | API 端点地址，默认为北京区域端点，具体可见附录-区域端点 |
| `dimension` | int | 是 | 向量维度，必须与模型输出及索引一致；本例选择 1024 |
| `input` | string | 否 | 输入类型：`"multimodal"`（多模态）或 `"text"`（纯文本），默认`"multimodal"` |

### 配置示例

将以下模型段落写入 `~/.openviking/ov.conf`，并保留已有的其他配置：

```json
{
  "vlm": {
    "provider": "volcengine",
    "api_key": "your-ark-api-key",
    "model": "doubao-seed-2-0-lite-260428",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3",
    "temperature": 0.1,
    "max_retries": 3
  },
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "api_key": "your-ark-api-key",
      "model": "doubao-embedding-vision-251215",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3",
      "dimension": 1024,
      "input": "multimodal"
    }
  }
}
```

将两处 `your-ark-api-key` 替换为第 3 步取得的 API Key。这里是模型凭据，与连接 OpenViking Server 的 user/admin key 不同。

## 验证配置

### 测试连接

先检查模型配置，再启动服务：

```bash
openviking-server doctor
openviking-server
```

另开终端，按[快速开始](../getting-started/02-quickstart.md)配置 CLI 并创建其中的 `quickstart.md`，然后导入到未使用的目录：

```bash
ov add-resource ./quickstart.md --to viking://resources/model-check --wait
ov find "谁负责备份流程？" --uri viking://resources/model-check
```

导入请求被接受不代表模型配置验证成功。等任务完成后再检索；超时可通过返回的 task ID 查询进度，失败则查看任务错误。

### 查看使用情况

在火山方舟控制台：

1. 访问 **"概览"** 页面
2. 查看 **Token 消耗统计**
3. 在 **"费用中心"** 查看账单明细

## 费用说明

### 计费方式

| 模型类型 | 计费单位 |
|---------|---------|
| VLM | 所选模型的输入/输出计费规则见[方舟价格说明](https://www.volcengine.com/product/ark) |
| Embedding | 所选模型及输入模态的计费规则见[方舟价格说明](https://www.volcengine.com/product/ark) |

### 免费额度

是否有赠送额度、适用模型、有效期及超额计费方式，以账户控制台和[火山方舟价格说明](https://www.volcengine.com/product/ark)为准。先用小文档观察实际 token 消耗，再估算批量导入成本；免费额度不保证覆盖任意规模的试用。

## 故障排除

### 常见错误

#### API Key 无效

```
Error: Invalid API Key
```

**解决方法**：
1. 检查 API Key 是否完整复制、有无多余空格
2. 确认 API Key 未被删除或过期
3. 重新创建 API Key

#### 模型未开通

```
Error: Model not activated
```

**解决方法**：
1. 在火山方舟控制台检查模型状态
2. 确认所选模型 ID 对当前账号可用
3. 检查账户余额是否充足

#### 网络连接问题

```
Error: Connection timeout
```

**解决方法**：
1. 检查网络连接
2. 确认 `api_base` 配置正确
3. 如在海外，确认可访问火山引擎服务
4. 服务可达但处理较慢时，再按[配置指南](01-configuration.md)调整相应超时

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

本文使用官方示例中的北京端点。其他地域请复制控制台给出的模型调用地址，不要只替换域名中的地域字符串。

### 模型版本对照

| 用途 | 本文示例模型 ID |
| --- | --- |
| VLM | `doubao-seed-2-0-lite-260428` |
| Embedding | `doubao-embedding-vision-251215` |

这些是示例版本，不代表最新版本。可用版本见[方舟模型发布记录](https://docs.volcengine.com/docs/ark/model-release-announcement?lang=zh)，Embedding 的输入和维度配置见[向量化文档](https://docs.volcengine.com/docs/ark/vectorization?lang=zh)。更换 Embedding 模型前，应检查现有索引是否需要重建。
