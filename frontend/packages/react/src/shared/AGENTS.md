# Shared Agent Guide

适用于本目录及其子目录，同时遵循 [React Agent Guide](../../AGENTS.md)。共享代码的职责与依赖约束见 [Frontend Agent Guide](../../../../AGENTS.md)。

## 组件组合

- 定义共享 UI 组件时，内容和选项优先通过 `children` 组合，由调用方控制渲染结构，避免用 `items`、`options` 等配置数组限定内容形态。
- 列表数据由调用方映射为子组件；组件应支持 Fragment、条件渲染及给整项包裹 Tooltip 等场景，不要求选项必须是直接子节点。
- 状态、行为配置和事件回调仍通过 props 传入，例如 `value`、`open`、`disabled` 和 `onChange`；组件组合与受控状态分别定义。
- 只有明确的数据处理或固定结构需求才采用数据型 props，不为同一份内容同时提供 `children` 与配置数组两套接口。
