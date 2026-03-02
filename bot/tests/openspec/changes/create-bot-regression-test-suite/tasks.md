# Tasks: Bot Regression Test Suite

## Phase 1: 基础设施

- [x] **Task 1.1**: 创建测试目录结构
  - Description: 创建测试目录结构和 conftest.py
  - Files:
    - `tests/__init__.py`
    - `tests/conftest.py`
    - `tests/unit/__init__.py`
    - `tests/integration/__init__.py`
    - `tests/fixtures/`

- [x] **Task 1.2**: 配置测试环境
  - Description: 配置 pytest 和覆盖率设置
  - Files:
    - `pyproject.toml` (添加 pytest 配置)
    - `.github/workflows/test.yml`

## Phase 2: 核心测试 (P0)

- [ ] **Task 2.1**: Agent Loop 测试
  - Description: 测试 Agent 核心处理流程
  - Files:
    - `tests/unit/test_agent/__init__.py`
    - `tests/unit/test_agent/test_loop.py`
  - Test Cases:
    - 测试消息处理基本流程
    - 测试工具调用循环
    - 测试会话管理集成
    - 测试错误处理和恢复

- [x] **Task 2.2**: 工具系统测试
  - Description: 测试工具注册和执行
  - Files:
    - `tests/unit/test_agent/test_tools.py`
    - `tests/unit/test_agent/test_tool_registry.py`
  - Test Cases:
    - 测试工具注册和发现
    - 测试参数验证
    - 测试工具执行和错误处理
    - 测试工具权限控制

- [x] **Task 2.3**: 上下文构建测试
  - Description: 测试系统提示词和上下文组装
  - Files:
    - `tests/unit/test_agent/test_context.py`
  - Test Cases:
    - 测试系统提示词生成
    - 测试记忆文件加载
    - 测试历史记录集成
    - 测试上下文长度限制

## Phase 3: 组件测试 (P1)

- [x] **Task 3.1**: Message Bus 测试
  - Description: 测试消息总线核心功能
  - Files:
    - `tests/unit/test_bus/__init__.py`
    - `tests/unit/test_bus/test_message_bus.py`
  - Test Cases:
    - 测试入队和出队操作
    - 测试消息路由
    - 测试订阅/发布模式
    - 测试背压和队列限制

- [x] **Task 3.2**: 渠道适配器测试
  - Description: 测试各消息渠道适配器
  - Files:
    - `tests/unit/test_channels/__init__.py`
    - `tests/unit/test_channels/test_telegram.py`
    - `tests/unit/test_channels/test_feishu.py`
    - `tests/unit/test_channels/test_discord.py`
  - Test Cases:
    - 测试消息接收和解析
    - 测试消息发送
    - 测试 webhook 处理
    - 测试错误重连逻辑

- [x] **Task 3.3**: 配置管理测试
  - Description: 测试配置验证和加载
  - Files:
    - `tests/unit/test_config/__init__.py`
    - `tests/unit/test_config/test_schema.py`
    - `tests/unit/test_config/test_loader.py`
  - Test Cases:
    - 测试配置 schema 验证
    - 测试环境变量覆盖
    - 测试配置文件加载
    - 测试默认值处理

## Phase 4: 集成测试 (P2)

- [x] **Task 4.1**: 端到端场景测试
  - Description: 测试完整的用户交互流程
  - Files:
    - `tests/integration/__init__.py`
    - `tests/integration/test_agent_e2e.py`
  - Test Cases:
    - 测试基本对话流程
    - 测试工具调用流程
    - 测试多轮对话记忆
    - 测试会话切换

- [ ] **Task 4.2**: 错误恢复测试
  - Description: 测试系统的容错和恢复能力
  - Files:
    - `tests/integration/test_error_recovery.py`
  - Test Cases:
    - 测试 LLM 服务不可用
    - 测试工具执行失败
    - 测试消息队列满
    - 测试配置错误处理

## Phase 5: CI/CD 集成

- [x] **Task 5.1**: GitHub Actions 工作流
  - Description: 配置自动化测试工作流
  - Files:
    - `.github/workflows/test.yml`
  - Features:
    - 多 Python 版本测试 (3.10, 3.11, 3.12)
    - 自动代码覆盖率报告
    - 测试失败通知

- [x] **Task 5.2**: 测试数据 fixtures
  - Description: 创建共享的测试数据和 fixtures
  - Files:
    - `tests/fixtures/messages.py`
    - `tests/fixtures/configs.py`
    - `tests/fixtures/sessions.py`
