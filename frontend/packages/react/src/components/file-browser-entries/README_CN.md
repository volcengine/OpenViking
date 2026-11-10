# FileBrowserEntries

[English](README.md) / 中文

必填 `locale: 'zh-CN' | 'en-US'` 选择内置中英文文案；已有 `labels` 可逐项覆盖，未覆盖项跟随当前语言。业务内容仍由宿主提供。

文件浏览区域顶部的入口条：连接 Agent 与导入资源。

固定两个入口。宿主只决定显示哪几个、点击后做什么；图标、文案、等分排布与两者之间的分隔线都由组件负责——这两项在每个宿主下外观都相同，差别只在点击行为。

```tsx
import { FileBrowserEntries } from '@openviking/react/components/file-browser-entries';
import '@openviking/react/components/file-browser-entries/style.css';

<FileBrowserEntries
  locale="zh-CN"
  onConnectAgent={openConnectDialog}
  onImportResources={openImportFlow}
/>;
```

## 显示与隐藏

回调即开关：省略 `onConnectAgent` 就隐藏连接入口，省略 `onImportResources` 就隐藏导入入口。

两个都省略时整块不渲染——宿主既没接连接也没接导入时，留一条空白底色只会让人以为还有内容没加载出来。

只显示一个入口时**不画分隔线**：线分隔的是两个相邻项，只剩一个就无从分隔。

```tsx
// 只显示导入入口，没有分隔线
<FileBrowserEntries locale="zh-CN" onImportResources={openImportFlow} />
```

## 文案

两项文案都可替换，默认跟随 locale：

```tsx
<FileBrowserEntries
  locale="zh-CN"
  onConnectAgent={openConnectDialog}
  onImportResources={openImportFlow}
  labels={{ connectAgent: '连接 Agent', importResources: '导入资源' }}
/>
```

## 与模块的关系

`FileBrowser` 的 `toolbarActions` 接受任意节点。用本组件放进去，入口条的外观就与模块其余部分一致：

```tsx
<FileBrowser locale="zh-CN"
  toolbarActions={
    <FileBrowserEntries locale="zh-CN" onConnectAgent={connect} onImportResources={import} />
  }
/>;
```

连接弹窗、导入流程与引导由宿主负责，组件只上报点击。

## 属性

| 属性                | 类型                       | 默认值 | 说明                               |
| ------------------- | -------------------------- | ------ | ---------------------------------- |
| `locale`            | `'zh-CN' \| 'en-US'`       | 必填   | 内置中英文文案。                   |
| `onConnectAgent`    | `() => void`               | —      | 连接入口被点击；省略时不显示该入口 |
| `onImportResources` | `() => void`               | —      | 导入入口被点击；省略时不显示该入口 |
| `labels`            | `FileBrowserEntriesLabels` | —      | 两项文案，未提供的项跟随 locale    |
| `className`         | `string`                   | —      | 容器附加类名                       |

## 主题

样式局部化，颜色取自包级主题基线。入口条的浅底由 `color-mix` 从主色派生，宿主换主色时跟着变。
