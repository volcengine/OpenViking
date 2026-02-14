# Providers 模块设计

## 概述

Providers 模块提供统一的 LLM 提供商接口，支持多个 LLM 服务（OpenAI、Anthropic、DeepSeek 等）。

## 模块结构

```
vikingbot/providers/
├── __init__.py
├── base.py              # LLM 提供商基类
├── registry.py          # 提供商注册表
├── litellm_provider.py  # LiteLLM 实现
└── transcription.py     # 语音转录（Groq Whisper）
```

## 核心组件

### 1. LLMProvider (LLM 提供商基类)

**文件**: `vikingbot/providers/base.py`

**职责**:
- 定义 LLM 提供商的抽象接口
- 提供统一的消息格式

**接口**:

```python
@dataclass
class ToolCallRequest:
    """LLM 发起的工具调用"""
    id: str
    name: str
    arguments: dict[str, Any]

@dataclass
class LLMResponse:
    """LLM 提供商的响应"""
    content: str | None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict[str, int] = field(default_factory=dict)
    reasoning_content: str | None = None  # Kimi, DeepSeek-R1 等
    
    @property
    def has_tool_calls(self) -> bool:
        """是否包含工具调用"""
        return len(self.tool_calls) > 0

class LLMProvider(ABC):
    """LLM 提供商抽象基类"""
    
    def __init__(self, api_key: str | None = None, api_base: str | None = None):
        self.api_key = api_key
        self.api_base = api_base
    
    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """
        发送聊天完成请求
        
        Args:
            messages: 消息列表（包含 role 和 content）
            tools: 可选的工具定义列表
            model: 模型标识符
            max_tokens: 最大响应 token 数
            temperature: 采样温度
            
        Returns:
            LLMResponse 对象
        """
        pass
    
    @abstractmethod
    def get_default_model(self) -> str:
        """获取默认模型"""
        pass
```

### 2. ProviderRegistry (提供商注册表)

**文件**: `vikingbot/providers/registry.py`

**职责**:
- LLM 提供商的单一事实来源
- 提供商元数据管理
- 模型名称匹配

**接口**:

```python
@dataclass(frozen=True)
class ProviderSpec:
    """LLM 提供商元数据"""
    
    # 身份标识
    name: str  # 配置字段名
    keywords: tuple[str, ...]  # 模型名关键字（用于匹配）
    env_key: str  # LiteLLM 环境变量
    display_name: str  # 显示名称
    
    # 模型前缀
    litellm_prefix: str  # 模型前缀（如 "openrouter/"）
    skip_prefixes: tuple[str, ...]  # 跳过前缀（避免双重前缀）
    
    # 额外环境变量
    env_extras: tuple[tuple[str, str], ...]  # 额外的 env var
    
    # 网关/本地检测
    is_gateway: bool  # 是否为网关（可路由任何模型）
    is_local: bool  # 是否为本地部署
    detect_by_key_prefix: str  # 通过 API key 前缀检测
    detect_by_base_keyword: str  # 通过 API base URL 关键字检测
    default_api_base: str  # 默认 API base URL
    
    # 网关行为
    strip_model_prefix: bool  # 是否剥离现有前缀再重新前缀
    
    # 模型参数覆盖
    model_overrides: tuple[tuple[str, dict[str, Any]], ...]  # 每模型参数覆盖
    
    @property
    def label(self) -> str:
        """显示标签"""
        return self.display_name or self.name.title()

# 查找函数
def find_by_model(model: str) -> ProviderSpec | None:
    """根据模型名匹配标准提供商"""
    
def find_gateway(
    provider_name: str | None = None,
    api_key: str | None = None,
    api_base: str | None = None,
) -> ProviderSpec | None:
    """检测网关/本地提供商"""
    
def find_by_name(name: str) -> ProviderSpec | None:
    """根据名称查找提供商"""
```

## 支持的提供商

### Gateways (网关提供商)

| 提供商 | 关键字 | 特性 |
|---------|--------|------|
| OpenRouter | openrouter | 路由任何模型，API key 以 "sk-or-" 开头 |
| AiHubMix | aihubmix | Open API 兼容，支持所有模型 |

### Standard Providers (标准提供商)

| 提供商 | 关键字 | 模型前缀 | 特性 |
|---------|--------|---------|------|
| Anthropic | anthropic, claude | 无 | LiteLLM 原生支持 |
| OpenAI | openai, gpt | 无 | LiteLLM 原生支持 |
| DeepSeek | deepseek | deepseek/ | 需要 "deepseek/" 前缀 |
| Gemini | gemini | gemini/ | 需要 "gemini/" 前缀 |
| Zhipu | zhipu, glm, zai | zai/ | 需要额外 env var |
| DashScope | qwen, dashscope | dashscope/ | 需要 "dashscope/" 前缀 |
| Moonshot | moonshot, kimi | moonshot/ | 需要 "moonshot/" 前缀 |
| MiniMax | minimax | minimax/ | 需要 "minimax/" 前缀 |

### Local Providers (本地提供商)

| 提供商 | 特性 |
|---------|------|
| vLLM | hosted_vllm/ | 本地 OpenAI 兼容服务器 |

### Auxiliary (辅助提供商)

| 提供商 | 用途 |
|---------|------|
| Groq | Whisper 语音转录 + LLM |

## 设计模式

### 策略模式

不同的 LLM 提供商通过统一接口互换，AgentLoop 不需要知道具体实现。

### 注册表模式

提供商元数据集中在 `ProviderRegistry.PROVIDERS`，添加新提供商只需修改注册表。

### 工厂模式

`find_by_model()` 和 `find_gateway()` 函数根据输入返回适当的提供商。

## 配置系统

### ProvidersConfig

```python
class ProvidersConfig(BaseModel):
    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)
    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    openrouter: ProviderConfig = Field(default_factory=ProviderConfig)
    deepseek: ProviderConfig = Field(default_factory=ProviderConfig)
    groq: ProviderConfig = Field(default_factory=ProviderConfig)
    zhipu: ProviderConfig = Field(default_factory=ProviderConfig)
    dashscope: ProviderConfig = Field(default_factory=ProviderConfig)
    vllm: ProviderConfig = Field(default_factory=ProviderConfig)
    gemini: ProviderConfig = Field(default_factory=ProviderConfig)
    moonshot: ProviderConfig = Field(default_factory=ProviderConfig)
    minimax: ProviderConfig = Field(default_factory=ProviderConfig)
    aihubmix: ProviderConfig = Field(default_factory=ProviderConfig)
```

### ProviderConfig

```python
class ProviderConfig(BaseModel):
    api_key: str = ""
    api_base: str | None = None
    extra_headers: dict[str, str] | None = None
```

## 提供商匹配逻辑

### 1. 模型名匹配

```python
# 在 Config.get_provider() 中
model_lower = (model or self.agents.defaults.model).lower()
for spec in PROVIDERS:
    p = getattr(self.providers, spec.name, None)
    if p and any(kw in model_lower for kw in spec.keywords) and p.api_key:
        return p, spec.name
```

### 2. 网关/本地检测

```python
# 优先级：provider_name > api_key_prefix > api_base_keyword
for spec in PROVIDERS:
    if spec.detect_by_key_prefix and api_key and api_key.startswith(spec.detect_by_key_prefix):
        return spec
    if spec.detect_by_base_keyword and api_base and spec.detect_by_base_keyword in api_base:
        return spec
```

### 3. 环境变量设置

```python
# 在 LiteLLM 调用前设置环境变量
os.environ[spec.env_key] = config.api_key
for key, value in spec.env_extras:
    os.environ[key] = value.format(api_key=config.api_key, api_base=config.api_base)
```

## 扩展点

### 添加新提供商

1. 在 `ProviderRegistry.PROVIDERS` 中添加 `ProviderSpec`
2. 在 `ProvidersConfig` 中添加配置字段

**示例**:

```python
# 在 registry.py 中
ProviderSpec(
    name="myprovider",
    keywords=("myprovider",),
    env_key="MYPROVIDER_API_KEY",
    display_name="My Provider",
    litellm_prefix="myprovider",
)

# 在 schema.py 中
class ProvidersConfig(BaseModel):
    # ... 其他提供商
    myprovider: ProviderConfig = Field(default_factory=ProviderConfig)
```

### 添加模型参数覆盖

在 `ProviderSpec` 中使用 `model_overrides` 字段：

```python
ProviderSpec(
    # ... 其他字段
    model_overrides=(
        ("my-model-v2", {"temperature": 1.0}),
    ),
)
```

## 错误处理

### LLM 调用错误

- 网络错误被捕获并记录
- 超时错误返回友好消息
- 不中断主流程

### 提供商未找到

当没有配置的 API key 时，返回 None 或使用默认提供商。

## 性能优化

### 连接复用

- LiteLLM 可能内部管理连接池
- 避免频繁创建/销毁连接

### 批量处理

- LiteLLM 支持批量请求（可选）

### 超时控制

- 所有 LLM 调用都有超时设置
- 防止长时间运行的请求

## 安全考虑

### API 密钥

- 存储在 `~/.vikingbot/config.json`
- 配置文件权限应设置为 600
- 不在日志中输出完整 API key

### 模型参数

- 使用 `model_overrides` 确保特定模型的正确参数
- 温度、max_tokens 等参数可配置

### 额外环境变量

- `env_extras` 支持设置额外的环境变量
- 用于提供商特定的配置需求
