# 跨模块 Hook

[English](README.md) / 中文

预留多个模块实际共用的 Hook，按真实复用需求增加实现。
公共 Hook 有实际实现后再从 `@openviking/react/hooks` 开放，不提供 CSS。业务模块专属的公开 Hook 留在 `modules/<模块>/`，从对应模块入口导出，供组合视图和宿主独立复用，不提升到本目录。私有 Hook 留在各模块内；组件使用的 Hook 同样遵守视图层边界，数据获取和业务编排由模块 Hook 或宿主负责。
当前仅提供目录说明，没有 Hook 实现或公开入口。

开发与构建入口见 [UI workspace README](../../../../README.md)，模块约定见[包说明](../../README.md)。
