---
'@openviking/react': patch
---

## en-US

Decode URI-derived names once for FileBrowser rows, preview tabs, search labels and FilePreview breadcrumbs. Keep original URIs for requests, selection and navigation; preserve explicit host labels and literal filesystem paths. Malformed escapes remain readable without throwing.

## zh-CN

FileBrowser 目录行、预览页签、搜索名称和 FilePreview 面包屑从 URI 派生名称时仅解码一次。请求、选择和导航保留原始 URI，宿主显式文案与普通文件路径保持原样；无效编码按原文显示，不抛出异常。
