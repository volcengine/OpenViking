---
'@openviking/react': minor
---

## en-US

Make FileBrowser preview operations native and resource-aware: content copying, original-byte downloads, direct user/peer experience and Compile history, Compile root configuration, directory ovpack import, ovpack export and confirmed file deletion. Apply namespace, ownership, summary-level, read-only and ACL rules before optional boolean `previewActions` visibility flags. Keep custom command lists and toolbar rendering available through `previewSlots`.

Remove host history/ovpack execution callbacks from preview action configuration. Add multipart FormData support to the native client, retryable workflow dialogs and bilingual workflow-label overrides.

## zh-CN

FileBrowser 的预览操作改为根据资源调用原生接口：支持复制内容、下载原始文件、查看用户和 Peer 的经验与编译历史、配置编译根目录、导入和导出 ovpack，以及确认后删除文件。先按命名空间、所有权、摘要层级、只读和 ACL 规则判断可用操作，再应用可选的 `previewActions` 显隐配置。保留 `previewSlots` 自定义命令列表和工具栏渲染。

移除预览操作配置中由宿主执行历史和 ovpack 操作的回调。原生客户端新增 multipart FormData 支持，流程弹窗支持失败重试及双语文案覆盖。
