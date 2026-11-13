---
'@openviking/react': patch
---

## en-US

Isolate rendering failures in blocks, file previews, command results and conversation regions. Local fallbacks preserve unaffected controls and state, with region-specific localized messages, display retry and identity-based recovery.

Add an optional `slots.errorFallback` to the affected components and blocks. Host content inherits into nested rendering boundaries, with explicit child overrides and built-in defaults when omitted.

Accept either a React node or a `(reset) => ReactNode` render function in error slots. Each failed region supplies its own reset action so host fallbacks can retry locally without resetting sibling state. FilePreview tab slots also receive the existing load retry action for read failures.

Retry failed lazy imports in file previews, message lists, composers and interaction forms with a fresh lazy instance, while keeping healthy instances stable across updates.

## zh-CN

隔离区块、文件预览、命令结果和对话区域的渲染错误，保留未受影响的控件与状态。默认错误展示按区域提供中英文提示，支持局部重试和展示对象变化后的恢复。

为相关业务组件和区块新增可选的 `slots.errorFallback`。嵌套错误边界继承宿主插槽，支持子组件覆盖，未配置时使用内置展示。

错误插槽支持 React 节点或 `(reset) => ReactNode` 渲染函数，每个失败区域提供自己的重置方法，重试时保留相邻区域的状态。FilePreview 页签插槽也会收到已有的读取重试方法。

文件预览、消息列表、输入框和交互表单的懒加载失败后，重试会创建新的懒加载实例；正常加载的实例在更新时保持稳定。
