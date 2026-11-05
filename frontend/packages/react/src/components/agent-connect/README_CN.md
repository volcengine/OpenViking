# AgentConnect

[English](README.md) / 中文

Agent 接入视图。宿主传入页签、当前页签的条目与选中项详情；组件渲染列表与详情，按需复制正文，并上报切换意图。

```tsx
import { AgentConnect } from '@openviking/react/components/agent-connect';
import '@openviking/react/components/agent-connect/style.css';

<AgentConnect
  tabs={tabs}
  activeTabId={activeTabId}
  items={items}
  selectedId={selectedId}
  detail={detail}
  onTabChange={setActiveTabId}
  onSelect={setSelectedId}
  onCopy={trackCopy}
/>;
```

目录从哪来、正文怎么渲染，都由宿主决定——组件不碰这些。复制正文是组件自己的职责。

## 为什么正文是「宿主给的节点」

接入说明天然是 Markdown 与代码块，且不同宿主的目录来源并不一致：一处从远程 JSON 拉取，另一处可能是本地文件。如果组件自己取数、自己渲染 Markdown，它就必须知道宿主的目录地址、缓存策略与 Markdown 方言，最后和某个文档站绑死。

这里反过来：**宿主提供条目与正文，组件只负责排版与上报**。

- 正文是节点，宿主可以传 Markdown 渲染结果、纯文本或任意组件。
- 复制用的纯文本与展示用的正文分开：展示可以带格式，复制必须是干净文本。
- 页签与选中项都是受控的，组件不缓存目录。

## 组件负责什么

- 渲染页签行与当前页签的高亮。
- 渲染左侧条目列表：图标、名称、一句话说明与选中态。
- 渲染右侧详情：标题、图标、说明、标签、正文与复制按钮。
- 复制：由组件完成，成功后通知已复制的文本。

## 组件不负责什么

- 不请求目录，不渲染 Markdown，不判断某项是否可接入。
- 不维护页签、选中项与正文——全部受控。
- 不接管路由与埋点：切换页签与选中条目只上报意图，`onCopy` 只是复制成功后的通知。
- 不内置任何具体 Agent 的名称或接入方式，条目完全由宿主声明。

## 文档与开发

- 属性表与可运行示例见[组件文档](../../../../../docs/docs/en/components/agent-connect.mdx)（[中文](../../../../../docs/docs/zh/components/agent-connect.mdx)）。
- 开发与构建命令见[前端工作区 README](../../../../../README.md)，模块约定见[包说明](../../../README_CN.md)。
- 独立样式入口 `@openviking/react/components/agent-connect/style.css`，与 JS 入口分离，可按需引入。

当前是占位组件：只渲染固定尺寸的空壳，不消费 props。上述为目标行为。
