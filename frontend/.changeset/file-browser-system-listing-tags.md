---
'@openviking/react': patch
---

Stop asking for retrieval tags when listing `viking://system`. That namespace is file-only and has no vector-store tags, so `include_tags` made the server reject the whole listing as an invalid argument and the directory opened as "load failed"; listings outside it still request tags for the Wiki dashboards.
