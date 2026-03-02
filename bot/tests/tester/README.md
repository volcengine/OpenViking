# Vikingbot 测试框架

这是一个针对 vikingbot 的基础功能测试框架。这些测试专注于验证核心功能的行为规范，不会因为 vikingbot 内部实现变化而改变。

## 位置

本测试框架位于: `bot/tests/tester/`

## 测试框架设计原则

1. **明确的测试目的** - 每个测试都有清晰的 spec 描述
2. **面向行为而非实现** - 测试验证功能行为，不依赖内部实现细节
3. **稳定性优先** - 基础功能测试不随 vikingbot 实现变化而变化
4. **独立可运行** - 测试可以独立运行，不依赖外部服务

## 目录结构

```
tests/tester/
├── README.md                          # 本文档
├── pyproject.toml                     # 项目配置
├── test_vikingbot.py                  # test_vikingbot 命令行工具
├── conftest.py                        # pytest 配置和 fixtures
├── pytest.ini                         # pytest 配置文件
├── specs/                             # 测试规范文档
│   └── agent_single_turn.md           # Agent 单轮对话测试规范
└── tests/
    ├── __init__.py
    └── test_agent_single_turn.py      # Agent 单轮对话测试
```

## 使用方法（推荐）

在 bot 目录下使用 `vikingbot test` 命令：

```bash
# 列出所有可用的测试
vikingbot test list

# 查看某个测试的详细规范
vikingbot test spec agent_single_turn

# 运行所有测试
vikingbot test run

# 运行指定的测试
vikingbot test run agent_single_turn

# 查看帮助
vikingbot test --help
```

## 直接使用 test_vikingbot.py

在 `tests/tester/` 目录下：

```bash
# 列出所有可用的测试
python3 test_vikingbot.py list

# 查看某个测试的详细规范
python3 test_vikingbot.py spec agent_single_turn

# 运行所有测试
python3 test_vikingbot.py run

# 运行指定的测试
python3 test_vikingbot.py run agent_single_turn
```

## 直接使用 pytest

在 `tests/tester/` 目录下：

```bash
# 运行所有测试
pytest tests/ -v

# 运行特定测试文件
pytest tests/test_agent_single_turn.py -v

# 运行带详细输出的测试
pytest tests/ -v -s
```
