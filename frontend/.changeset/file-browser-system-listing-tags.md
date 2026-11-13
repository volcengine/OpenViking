---
'@openviking/react': patch
---

## en-US

Stop asking for retrieval tags when listing `viking://system`. That namespace is file-only and has no vector-store tags, so `include_tags` made the server reject the whole listing as an invalid argument and the directory opened as "load failed"; listings outside it still request tags for the Wiki dashboards.

## zh-CN

列出 `viking://system` 时不再请求检索标签。该命名空间仅包含文件，没有向量库标签；传入 `include_tags` 会使服务端以无效参数拒绝整个目录列表，导致页面显示加载失败。其他命名空间继续请求标签，供 Wiki 看板使用。
