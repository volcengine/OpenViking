---
'@openviking/react': patch
---

## en-US

- Show Version history only for experience files and Wiki nodes; ordinary text files, server-managed settings such as `settings/user_config.json` and other resources no longer offer it.
- Remove the post-save manual snapshot flow, its labels and the `POST /api/v1/snapshot/commit` call. Wiki versions keep coming from compilation and edit saves.
- Show `viking://` on a first-level directory's breadcrumb instead of only its name.

## zh-CN

- 版本历史只在经验文件和 Wiki 节点上显示；普通文本文件、`settings/user_config.json` 这类服务端配置和其他资源都不再提供。
- 移除保存后的手动快照流程、相关文案与 `POST /api/v1/snapshot/commit` 调用；Wiki 版本仍由编译和编辑保存产生。
- 一级目录的面包屑显示 `viking://` 前缀，不再只剩目录名。
