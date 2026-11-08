# VikingBot

[English](README.md) / 中文

对话视图：消息列表、输入区与待确认交互区。开源版和火山版两套宿主协议不同，组件只接收已归一化的消息模型，并把两版渲染方式不同的区域交给宿主替换。

> **当前状态：占位实现。** API 契约已确定，并按契约渲染出可用视图，但视图尚未经过设计评审，真实实现会被重写。请把 props 与交互模型当作交付物，视觉表现视为临时方案。

`viking-bot.tsx` 对应 `@openviking/react/components/viking-bot`；待确认交互区对应 `@openviking/react/components/viking-bot/interaction`。

## 为什么一套模型能接两版

开源版流式下发 `parts` 数组、用自己的技术栈渲染 Markdown；火山版下发 Managed Agent 事件、用 Ark Design 渲染执行时间线。两者的消息形态、流式语义、以及是否存在人工确认步骤都不一样。

因此组件只拥有**两版共有的部分**：

- 消息是 `{ id, role, parts }`，`role` 覆盖 `user`、`assistant`、`system` 与 `tool`。
- 每个片段自带判别值，`groupId` 把需要合并折叠的片段归入同一个执行块。
- 流式是 `{ parts, insertIndex, iteration }`；在末尾追加的宿主把 `insertIndex` 传成消息数量即可。
- 渲染方式不同的宿主替换 `messageList`、`composer` 或 `interaction`，而不是改造内置视图。

宿主协议的适配留在各宿主仓库：组件不引入任何后端事件类型。

## 组件负责

- 渲染消息列表，包括推理、工具调用与结果、上下文引用、图片、附件与迭代分隔。
- 复制消息正文并上报意图，复制动作由宿主执行。
- 渲染输入区、附件列表与发送/停止切换。
- 渲染 `choice`、`text`、`schedule`、`repair` 四种内置交互表单。
- 合并同一轮的连续助手消息，并按 `groupId` 归并片段。

## 宿主负责

- 取数、流式连接、会话创建与持久化。
- 把自身事件归一化成包内消息模型。
- 上传附件；组件只上报用户选择或拖入的文件。
- 判定轮次结束及其后续动作。
- 需要树选择器、上传器等宿主能力时，用 `custom` 交互自行渲染。

## 替换区域

```tsx
<VikingBot
  messages={messages}
  streaming={streaming}
  draft={draft}
  onDraftChange={setDraft}
  onSend={send}
  onCancel={abort}
  messageList={<MyActivityTimeline messages={messages} />}
  composer={<MySender value={draft} onChange={setDraft} onSend={send} />}
/>
```

`children` 替换整个主体。`interaction` 优先于 `composer`，两版宿主在待确认时都是这个行为。

## 交互

待确认问题是宿主状态，不是后端暂停：宿主从自己的流里算出问题，答案发回后清除。组件只渲染表单并上报答案，宿主决定怎么发回去。

`choice`、`text`、`schedule`、`repair` 由组件内置渲染。`custom` 收到 `{ disabled, submitting, submit, cancel }` 后自行渲染，因此树选择器、Skill 上传器这类能力不需要进入公共库。

## 开发入口

开发与构建入口见 [前端工作区 README](../../../../../README.md)，模块约定见[包说明](../../../README.md)。

独立样式入口为 `@openviking/react/components/viking-bot/style.css`，包含消息列表、输入区与交互区样式。
