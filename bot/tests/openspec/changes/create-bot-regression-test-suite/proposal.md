# Proposal: Create Bot Regression Test Suite

## Why

随着 Vikingbot 项目功能不断扩展，需要一个自动化的测试回归集合来保证核心功能在迭代开发中保持稳定。目前项目缺乏系统性的测试覆盖，导致代码变更时难以快速验证是否引入回归问题。

## What Changes

- 建立完整的测试目录结构（unit/integration/fixtures）
- 为核心模块编写单元测试（Agent Loop、工具系统、Message Bus）
- 为渠道适配器编写测试（Telegram、Feishu、Discord）
- 添加配置验证和会话管理测试
- 创建端到端集成测试场景
- 配置 GitHub Actions CI 工作流
- 生成代码覆盖率报告

## Capabilities

### New Capabilities
- `regression-test-suite`: 完整的回归测试集合，包含单元测试、集成测试和 fixtures

### Modified Capabilities
- 无

## Impact

- 新增测试代码不会影响生产功能
- CI/CD 流程新增测试步骤，可能增加构建时间 2-3 分钟
- 开发人员需要遵循测试编写规范
- 代码覆盖率报告将公开在 PR 中展示
