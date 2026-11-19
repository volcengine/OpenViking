---
'@openviking/react': patch
---

## en-US

Add `OpenVikingProvider.userRootMode="current"` so directory trees display the resolved current user without listing every user. Load the user's contents when expanded, inherit the mode through nested Providers, and keep native listings when no user identity is available.

## zh-CN

新增 `OpenVikingProvider.userRootMode="current"`，目录树直接展示已解析的当前用户，展开具体用户目录时再加载内容，避免枚举全部用户。嵌套 Provider 继承此模式，缺少用户身份时仍使用原生目录请求。
