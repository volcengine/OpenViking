# 内部 Hook

Hook 是组件或模块的内部实现，放在对应目录内。实际跨组件复用的 Hook 放在 `src/shared/`。当前不提供 `@openviking/react/hooks` 或业务无头 Hook 公开入口。

取数 Hook 仅位于模块内；组件私有 Hook 只管理本地 UI 交互，不感知接口。
