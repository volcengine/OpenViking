# FilePreview 文件预览

[English](README.md) / 中文

控制台式文件页签、面包屑、分级内容、图片预览、源码与编辑。组件和类型从 `@openviking/react/components/file-preview` 导入，样式需显式导入 `@openviking/react/components/file-preview/style.css`。

文件页签接收 `source`，可选 `levels`（L0/L1/L2 或宿主自定义标签）与 `resolveImage(src, source)`，后者用于鉴权或相对路径 Markdown 图片。每级提供独立内容，可覆盖资源与编辑能力。宿主负责取数与返回 URL 的生命周期。原始图片使用二进制 `file` 或可访问 `url`。动作与权限标记由宿主插槽提供，页脚可放更新时间和内容解析说明。

查看器按需加载，外壳支持 SSR。完整接口和控制台示例见 `frontend/docs/docs/` 中的中英文组件文档。
