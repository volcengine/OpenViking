
OpenCode 有两个设计路径不同的插件变体，请按你的使用方式自行选择。

## 方式 1：`opencode-memory-plugin` — 显式工具版本

源码：[examples/opencode-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/opencode-memory-plugin)

通过 OpenCode 的工具机制把 OpenViking 记忆暴露为显式工具。模型决定何时调用，数据按需获取。

## 方式 2：`opencode/plugin` — 上下文注入版本

源码：[examples/opencode/plugin](https://github.com/volcengine/OpenViking/tree/main/examples/opencode/plugin)

把已索引的代码仓库注入 OpenCode 上下文，并按需自动启动 OpenViking 服务器。
