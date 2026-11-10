# FileBrowserEntries

[English](README.md) / 中文

必填 `locale: 'zh-CN' | 'en-US'` 选择内置中英文文案；已有 `labels` 可逐项覆盖，未覆盖项跟随当前语言。业务内容仍由宿主提供。

文件浏览区域顶部的入口条：连接 Agent 与导入资源。

固定两个入口。传入 `agentConnect` 使用内置右侧接入抽屉，或通过回调接入宿主流程。图标、文案、等分排布与分隔线由组件负责。

```tsx
import { FileBrowserEntries } from '@openviking/react/components/file-browser-entries';
import '@openviking/react/components/file-browser-entries/style.css';

<FileBrowserEntries locale="zh-CN"
  agentConnect={{ apiKey: 'demo-api-key', locale: 'zh-CN' }}
  onImportResources={openImportFlow}
/>;
```

## 显示与隐藏

传入 `agentConnect` 即可打开内置的 `AgentConnect` 右侧抽屉。参数直接透传给接入面板，其中 `apiKey` 和 `locale` 为必填项；入口条样式已包含面板样式，无需额外导入。抽屉从右侧滑出，可通过关闭按钮、遮罩或 Esc 关闭；窄容器内可横向滚动面板。

`onConnectAgent` 仍支持宿主自定义流程；与 `agentConnect` 同时传入时，作为点击通知触发。两者都省略才隐藏连接入口；同时省略 `onImportResources` 和 `renderImportResources` 隐藏导入入口。

没有可见入口时整块不渲染。

只显示一个入口时**不画分隔线**：线分隔的是两个相邻项，只剩一个就无从分隔。

```tsx
// 只显示导入入口，没有分隔线
<FileBrowserEntries locale="zh-CN" onImportResources={openImportFlow} />
```

## 文案

入口文案及抽屉 `close` 文案均可替换，未覆盖项跟随 locale：

```tsx
<FileBrowserEntries locale="zh-CN"
  agentConnect={{ apiKey: 'demo-api-key', locale: 'zh-CN' }}
  onImportResources={openImportFlow}
  labels={{
    connectAgent: '连接 Agent',
    importResources: '导入资源',
    close: '关闭',
  }}
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

宿主提供接入凭证并处理资源导入。需要自定义接入流程时，仅传入 `onConnectAgent`，不传 `agentConnect`。

## 自定义导入入口

`renderImportResources(trigger)` 接收已包含图标、文案和 `onImportResources` 回调的原生按钮。宿主可以用自己的 `Popover` 包裹它，或返回替代节点；无需为了显示入口传入空回调。返回 `null` 时隐藏入口及对应分隔线，所有入口都隐藏时整块不渲染。

下面的 `Popover` 与 `ImportMenu` 由宿主提供。默认按钮直接作为 `Popover` 的 `children`，供其接管 ref、事件与定位。保留默认按钮时会保留点击回调；完全替换节点时，由宿主绑定交互。

```tsx
<FileBrowserEntries locale="zh-CN"
  renderImportResources={(trigger) => (
    <Popover trigger="click" content={<ImportMenu />}>
      {trigger}
    </Popover>
  )}
  labels={{ importResources: '导入资源' }}
/>
```

## 属性

| 属性                    | 类型                                   | 默认值 | 说明                                     |
| ----------------------- | -------------------------------------- | ------ | ---------------------------------------- |
| `locale` | `'zh-CN' \| 'en-US'` | 必填 | 内置中英文文案。 |
| `agentConnect`          | `AgentConnectProps`                    | —      | 启用内置右侧接入抽屉                     |
| `onConnectAgent`        | `() => void`                           | —      | 点击通知；单独使用时接入宿主自定义流程   |
| `onImportResources`     | `() => void`                           | —      | 默认导入按钮的点击回调                   |
| `renderImportResources` | `(trigger: ReactElement) => ReactNode` | —      | 包装或替换默认导入按钮；返回 `null` 隐藏 |
| `labels`                | `FileBrowserEntriesLabels`             | —      | 入口及抽屉 `close` 文案，未覆盖项跟随 locale      |
| `className`             | `string`                               | —      | 容器附加类名                             |

## 主题

样式局部化，颜色取自包级主题基线。入口条的浅底由 `color-mix` 从主色派生，宿主换主色时跟着变。

## 抽屉实现

内部通用组件 `Drawer` 位于 `shared/components/drawer`，参考 [Arco Design Drawer](https://github.com/arco-design/arco-design/tree/main/components/Drawer)。它通过 `children` 接收内容，`FileBrowserEntries` 负责传入 `AgentConnect`。保留 body Portal、遮罩、300ms 右侧滑入动画、焦点锁与滚动锁。省去其他方向、页脚操作、尺寸拖动和容器配置，不使用浏览器原生 dialog。Arco 的 MIT 声明随包保留在 `LICENSE.arco-design`，本包许可证仍为 AGPL-3.0-only。
