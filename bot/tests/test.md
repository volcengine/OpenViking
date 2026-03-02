# Vikingbot 测试框架文档

## 概述

Vikingbot 测试框架是针对 vikingbot 核心功能的行为驱动测试框架。

**测试框架位置**: `bot/tests/tester/`

---

## 目录结构

```
tests/
├── test.md                          # 本文档
├── tester/                          # 测试框架根目录
│   ├── README.md                    # 测试框架说明
│   ├── pytest.ini                   # pytest 配置
│   ├── test_vikingbot.py            # 测试命令行工具
│   ├── specs/                       # 测试规范文档
│   │   └── agent_single_turn.md
│   └── tests/                       # 测试用例目录
│       └── test_agent_single_turn.py
├── openviking_mount/
└── sandbox/
```

---

## 使用方法

### 方式一：使用 vikingbot test 命令（推荐）

```bash
# 列出所有可用的测试
vikingbot test list

# 运行所有测试
vikingbot test run

# 查看帮助
vikingbot test --help
```

### 方式二：直接使用 pytest

在 `tests/tester/` 目录下执行：

```bash
pytest tests/ -v
```

---

## 测试用例列表

### 测试组: Agent 单轮对话 (`agent_single_turn`)

**测试文件**: `tests/tester/tests/test_agent_single_turn.py`

**测试目的**: 验证 `vikingbot chat` 单聊功能是否正常工作

| 用例ID | 用例名称 | 测试命令 | 规格说明 |
|--------|---------|---------|---------|
| 1 | `vikingbot chat -m 正常` | `vikingbot chat -m "消息"` | vikingbot chat -m 参数可以正常发送消息并获取回复 |
| 2 | `vikingbot chat 正常` | `vikingbot chat` | vikingbot chat 交互模式可以正常启动和运行 |

---

## 测试配置

### pytest 配置

**主项目配置** (`pyproject.toml`):
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

**tester 配置** (`tests/tester/pytest.ini`):
```ini
[pytest]
asyncio_mode = auto
testpaths = tests
pythonpath = ../..
addopts = -v --tb=short
```

### 测试依赖

- `pytest>=7.0.0`
- `pytest-asyncio>=0.21.0`

---

## Fixtures 列表

`conftest.py` 中提供的共享 fixtures:

| Fixture 名称 | 说明 | 返回值 |
|-------------|------|--------|
| `sample_session_key` | 提供标准的测试用 SessionKey | `SessionKey(type="test", channel_id="test_channel", chat_id="test_chat")` |
| `another_session_key` | 提供另一个不同的 SessionKey | `SessionKey(type="test", channel_id="test_channel", chat_id="another_chat")` |
| `fixed_datetime` | 提供固定的 datetime | `datetime(2024, 1, 1, 12, 0, 0)` |
| `temp_dir` | 提供临时目录 | `Path` 对象（基于 tmp_path） |

---

## 相关文件索引

| 文件 | 位置 | 说明 |
|-----|------|------|
| **README.md** | `tests/tester/README.md` | 测试框架原始说明文档 |
| **test_vikingbot.py** | `tests/tester/test_vikingbot.py` | 测试命令行工具 |
| **conftest.py** | `tests/tester/conftest.py` | pytest fixtures |
| **pytest.ini** | `tests/tester/pytest.ini` | pytest 配置 |
| **test_commands.py** | `vikingbot/cli/test_commands.py` | CLI 集成模块 |
| **agent_single_turn.md** | `tests/tester/specs/agent_single_turn.md` | 单轮对话测试规范 |
| **test_agent_single_turn.py** | `tests/tester/tests/test_agent_single_turn.py` | 单轮对话测试用例 |
