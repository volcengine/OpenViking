---
'@openviking/react': minor
---

## en-US

Add native TanStack Query v5 client reuse and per-endpoint request concurrency through OpenVikingProvider. Directory trees, search-scope pickers and searches share scoped caches, preserve loaded rows after background refresh failures, and invalidate caches after directory writes. Restored caches remain isolated from new identity scopes. The SDK includes its default query runtime without requiring hosts to install React Query.

## zh-CN

OpenVikingProvider 支持复用原生 TanStack Query v5 客户端，并按接口配置请求并发。目录树、搜索范围选择器及搜索共用作用域缓存，后台刷新失败保留已加载行，目录写入后使缓存失效。恢复的缓存不会跨身份复用。SDK 内置默认查询运行时，宿主无需另行安装 React Query。
