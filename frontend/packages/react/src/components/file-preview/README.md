# FilePreview

多格式文件预览。宿主给出内容与文件名，组件负责格式识别、渲染器加载、标题栏、页签、视图切换与编辑。

> **当前是占位实现。** 外壳（页签行、内容区标题行、页签切换、复制、编辑、ref 句柄）
> 已按最终形态实现，可以直接交互；文件正文尚未接入 FileViewer，文件页签暂以纯文本
> 降级展示。下文描述的是目标行为。

调用方不需要知道这是 Markdown 还是 PDF，也不需要注册渲染器——文件名决定一切：

```tsx
<FilePreview
  tabs={[
    {
      kind: 'file',
      key: 'file',
      source: { content, fileName: 'SKILL.md', path: 'viking://…/SKILL.md' },
    },
  ]}
/>
```

## 为什么以 FileViewer 为底座

多格式预览的难点不在渲染单个格式，而在格式路由、渲染器按需加载、大文件准入与状态机。这些已经有成熟实现，重写一遍只会得到一份更差的版本。

因此本组件以 [`@ark-design/ui`](https://www.npmjs.com/package/@ark-design/ui) 的 `FileViewerCore` 作为渲染底座，在它之上补齐 OpenViking 需要的部分：标题行、页签、视图切换、编辑与业务插槽。

`@ark-design/ui` 是**可选 peer dependency**：只有使用本模块的消费方需要安装，不装时其余模块不受影响。

```bash
npm install @ark-design/ui
```

内置渲染器覆盖代码、纯文本、JSON、Markdown、HTML、表格、PDF、Word、PowerPoint、音视频、CAD 与 3D 模型。无扩展名的文件按文件名匹配，例如 `Dockerfile` 与 `Makefile`。支持范围固定，不对外开放覆盖。

## 内容由页签驱动

预览的内容是页签列表，每个页签的正文有两种来源：

| 类型              | 渲染路径                 | 典型用途                      |
| ----------------- | ------------------------ | ----------------------------- |
| `kind: 'file'`    | 经 FileViewer 按格式渲染 | 文件原文、源码、文档          |
| `kind: 'content'` | 完全由宿主渲染           | 经验看板、wiki 看板、目录摘要 |

两类页签共享外壳——页签行、内容区标题行（路径面包屑、复制、权限标记、标题行插槽），差异只在正文怎么来。当前激活哪个页签由组件内部维护，宿主不感知；需要从外部跳转时用 ref 句柄的 `setActiveTab`。

选中目录时首个页签可以是 L0/L1 摘要这类内容页签，因此组件不假设第一个一定是文件。

## 状态由组件判定

加载中、加载失败与格式不支持不通过 props 表达，组件根据内容与文件类型自行判定：没有内容即加载中，内容带错误即失败，文件名没有匹配的渲染器即不支持。三种状态都可以通过插槽换成宿主自己的内容。

编辑态同理：草稿、保存中与失败提示都在组件内部。宿主通过 `editing.loadContent` 提供完整文本，通过异步 `editing.save` 执行保存；`editing.onSave` 仅在保存成功后通知已保存的文本，失败时保留草稿并显示提示。`editing.onCancel` 在草稿被丢弃后通知，保存成功不触发它。

执行能力与成功通知分开：文件操作由 `operations.openParent`、`operations.download`、`operations.openUri` 提供，成功后才触发对应的 `onOpenParent`、`onDownload`、`onOpenUri`。省略执行方法时该操作不可用，省略通知不影响操作。失败时不触发成功通知。

复制由组件完成。`onCopyPath` 与 `onCopyContent` 分别在路径、正文复制成功后通知已复制的值，失败时不触发；省略回调不影响复制。提供路径时显示路径复制按钮，文件页签的正文复制按钮在没有可复制文本时禁用。

## 组件不负责什么

- 不发起业务请求。下载、保存与导航由宿主提供执行能力；成功通知不负责执行这些操作。
- 不判断谁能看、谁能改——权限标记由宿主渲染后从插槽传入。
- 不内置业务内容。看板、图谱、摘要都通过页签或插槽注入，组件不知道它们是什么。

## 文档与开发

- 属性表与可运行示例见文档站 `Components → FilePreview`（待随实现补充）。
- 开发与构建命令见[前端工作区 README](../../../../../README.md)，模块约定见[包说明](../../../README.md)。
- 独立样式入口 `@openviking/react/components/file-preview/style.css`，与 JS 入口分离，可按需引入。
