# FilePreview

[English](README.md) / 中文

必填 `locale: 'zh-CN' | 'en-US'` 选择内置中英文文案；已有 `labels` 可逐项覆盖，未覆盖项跟随当前语言。业务内容仍由宿主提供。

多格式文件预览。宿主给出内容与文件名，组件负责格式识别、渲染器加载、标题栏、页签、视图切换与编辑。

`index.ts` 对应 `@openviking/react/components/file-preview`。入口、样式与包导出已预留，尚未导出组件或类型。实现按本文重写，字段以源码为准。

## 开发入口

开发与构建入口见 [前端工作区 README](../../../../../README.md)，模块约定见[包说明](../../../README.md)。

独立样式入口为 `@openviking/react/components/file-preview/style.css`，迁入实现前为空样式。
