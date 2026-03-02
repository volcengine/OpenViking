# Design: Bot Regression Test Suite

## Overview

基于 pytest 构建分层测试架构，使用 unittest.mock 和 pytest-asyncio 支持异步测试，确保 Vikingbot 各组件的正确性和稳定性。

## Test Architecture

```
tests/
├── conftest.py              # 全局 fixtures 和配置
├── unit/                    # 单元测试
│   ├── test_agent/
│   │   ├── test_loop.py     # Agent loop 测试
│   │   ├── test_tools.py    # 工具注册和执行
│   │   └── test_context.py  # 上下文构建
│   ├── test_bus/
│   │   └── test_message_bus.py
│   ├── test_channels/
│   │   ├── test_telegram.py
│   │   └── test_feishu.py
│   └── test_config/
│       └── test_schema.py
├── integration/             # 集成测试
│   └── test_agent_e2e.py    # 端到端场景
└── fixtures/                # 测试数据
    ├── messages.json
    └── configs.yaml
```

## Key Testing Patterns

### 1. Async Testing
```python
import pytest

@pytest.mark.asyncio
async def test_agent_loop():
    # 使用 pytest-asyncio 处理异步
    result = await agent.process_message(msg)
    assert result is not None
```

### 2. Mock External Dependencies
```python
from unittest.mock import AsyncMock, patch

@patch('vikingbot.channels.telegram.Bot')
async def test_telegram_send(mock_bot):
    mock_bot.return_value.send_message = AsyncMock()
    await channel.send(msg)
    mock_bot.return_value.send_message.assert_called_once()
```

### 3. Fixture-Based Test Data
```python
@pytest.fixture
def sample_message():
    return Message(
        id="test-001",
        content="Hello",
        user_id="user-001",
        channel="telegram"
    )
```

## CI Integration

```yaml
# .github/workflows/test.yml
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv pip install -e ".[dev]"
      - run: pytest --cov=vikingbot --cov-report=xml
      - uses: codecov/codecov-action@v4
```

## Running Tests

```bash
# 所有测试
pytest

# 仅单元测试
pytest tests/unit

# 带覆盖率
pytest --cov=vikingbot --cov-report=html

# 特定文件
pytest tests/unit/test_agent/test_loop.py -v
```

## Implementation Priority

1. **P0**: Agent loop 核心测试
2. **P0**: 工具注册和执行测试
3. **P1**: Message bus 测试
4. **P1**: 渠道适配器测试
5. **P2**: 配置验证测试
6. **P2**: 集成场景测试

## Test Data Management

- 使用 JSON/YAML 文件存储测试数据
- Fixture 函数加载和缓存数据
- 敏感信息使用环境变量或 mock

## Coverage Goals

- 核心模块: 80%+
- 渠道适配器: 70%+
- 整体项目: 75%+
