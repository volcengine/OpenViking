# VikingBot

[English](README.md) / 中文

对话视图：消息列表、输入区与待确认交互区。渲染使用 **Ark Design**（`@ark-design/ui`）——与火山版控制台同一套组件库——只接收已归一化的消息模型，宿主协议不进入组件。

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

- 用 Ark 的 `ChatMessageList`、`Bubble`、`ActivityTimeline`、`MDRenderer`、`ErrorCard` 渲染消息列表。
- 用 Ark 的 `Sender` 渲染输入区。
- 用 Ark 的 `AskHuman` 渲染 `choice`、`text`、`schedule`、`repair` 四种内置交互表单。
- 复制消息正文并上报意图，复制动作由宿主执行。
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

`children` 替换整个主体。`interaction` 优先于 `composer`，两版宿主在待确认时都是这个行为；宿主自带交互区时用 `interactionSlot` 整块替换。

省略 `draft` 时不渲染输入区，只读会话因此不会多出一个不可用的输入框。

## 交互

待确认问题是宿主状态，不是后端暂停：宿主从自己的流里算出问题，答案发回后清除。组件只渲染表单并上报答案，宿主决定怎么发回去。

`choice`、`text`、`schedule`、`repair` 由组件内置渲染。`custom` 收到 `{ disabled, submitting, submit, cancel }` 后自行渲染，因此树选择器、Skill 上传器这类能力不需要进入公共库。

## Ark 走懒加载

Ark 依赖浏览器专用模块，在纯 Node 进程里无法解析，因此所有用到它的入口都放在 `lazy()` 之后——与 `FilePreview` 正文入口同一做法。包的顶层入口在没有 `window` 的环境下可以正常导入，Ark 被编译进独立 chunk，`@ark-design/ui` 保持 optional peer。

## 开发入口

开发与构建入口见 [前端工作区 README](../../../../../README.md)，模块约定见[包说明](../../../README.md)。

独立样式入口为 `@openviking/react/components/viking-bot/style.css`，引入主题基线与视图用到的 Ark 组件样式。
