---
'@openviking/react': minor
---

## en-US

Replace component-specific content-copy/download commands with host-defined FilePreview actions, ordered array groups, icon-only single actions, hover menus and asynchronous feedback. Remove the legacy menu API and compatibility adapter. Keep built-in path copying, view switching and editing, with root/per-tab visibility switches and the internal Dropdown.

Render images and videos independently of FileViewer using native media elements. Retain image enlargement, loading/error/retry feedback and owned Blob URL cleanup; media previews have no source mode or text editing.

Preserve FileViewer’s default unsupported-file download UI and custom `downloadFallback` callbacks. Native media honors custom loading, error and missing-source fallbacks.

## zh-CN

FilePreview 用宿主定义的操作替代专用的内容复制和下载命令，支持有序分组、单操作图标按钮、悬浮菜单和异步反馈。移除旧菜单 API 及兼容适配器。保留内置路径复制、视图切换和编辑，以及整体或单页签的显隐配置。

图片和视频改用原生媒体元素独立渲染。保留图片放大、加载、错误与重试提示，以及自建 Blob URL 的清理；媒体预览不提供源码模式和文本编辑。

保留 FileViewer 默认的不支持文件下载界面及自定义 `downloadFallback` 回调。原生媒体支持自定义加载、错误和缺少源文件时的提示。
