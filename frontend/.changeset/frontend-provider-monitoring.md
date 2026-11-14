---
'@openviking/react': minor
---

## en-US

Add opt-in, strongly typed frontend monitoring through OpenVikingProvider, covering component failures, resources, requests, streams, block loading and task polling. Failure events identify component instances and include bounded frontend trace snapshots. Add bilingual integration guidance and an event catalogue. Component and block Props retain their existing interaction interfaces.

Target browser CSR only and remove SSR-specific rendering and hydration paths. Mount components in the browser; the documentation site also uses CSR.

## zh-CN

通过 OpenVikingProvider 新增默认关闭、强类型的前端监控，覆盖组件异常、资源、请求、流、区块加载和任务轮询。失败事件标识组件实例并附带有容量限制的前端链路快照。补充中英文接入指南和事件目录，业务组件与区块保持原有交互接口。

当前仅支持浏览器 CSR，移除为 SSR 和 hydration 提供的分支与检查。组件在浏览器中挂载，文档站同步采用 CSR。
