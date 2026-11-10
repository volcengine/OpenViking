# AgentConnect

[English](README.md) / 中文

按 [Figma 节点 10468:23946](https://www.figma.com/design/ecpd1ZGHKRYibm1ODKEpM6/OpenViking-Console?node-id=10468-23946) 实现的接入视图。标题、两个页签、十四个 Agent 与四个开发接入方式的名称、图标、说明、标签及正文均内置。调用方传入 `apiKey`；`locale: 'zh-CN' | 'en-US'` 可选，省略时继承上层语言，最终默认 `en-US`。`baseUrl` 可覆盖默认云服务地址。

```tsx
import { AgentConnect } from '@openviking/react/components/agent-connect';
import '@openviking/react/components/agent-connect/style.css';

<AgentConnect apiKey={apiKey} locale="zh-CN" />;
```

## 设计与交互约定

- 安装器命令显式指定当前 Agent 的 `--harness`，并包含接入引导需要的 `cli`；Claude Code 对应 `claude`、Kimi Code 对应 `kimicode`、DeepSeek Harness 对应 `dsh`，TRAE 对应国际版 `trae`。
- 安装器命令中的服务地址和密钥使用 Shell 单引号转义，执行后参数保持调用方字面值；不将 Shell 转义应用到 JSON、Python 或纯文本指南。自定义 Hermes 地址使用 **Custom** 连接步骤，并填写该地址。
- 指南追加 `/mcp` 等子路径时移除服务地址末尾的斜杠；API 示例的 `url` 变量也使用该规范化地址，以便随后拼接 `/api/v1/...`。安装器参数和不拼接路径的独立地址保留调用方原值。
- 指南、凭据或地址变化会创建新的复制交互实例：清空反馈和 pending 状态，返回旧指南也不会恢复旧反馈。已发起的 Clipboard API 写入无法取消，但过期完成结果不更新界面或调用 `onCopy`。
- 宿主可提供 `agreementUrl`，协议引用渲染为可用鼠标和键盘访问的链接；未提供时仅显示数据访问提示，不显示无目标的协议引用。

- 语言仅由调用方通过 `locale` 参数指定，组件和示例不提供语言选择 UI。
- 默认 1000px 宽、949px 最小高度，左侧列表 200px，列间距 40px，详情宽 660px；标题、胶囊页签、行高、图标尺寸、指令排版和光晕以设计截图为准。
- 默认选中「接入主流 Agent」与 Codex。页签和条目使用实例内状态，也可通过 `activeTabId`、`selectedId` 控制，并通过可选回调通知宿主。页签支持方向键、Home、End。
- 主流页签展示完整本地目录：Codex、Claude Code、豆包工作、WorkBuddy、TRAE、Cursor、OpenClaw、Hermes Agent、OpenCode、DeepSeek Harness、DeerFlow、pi、ZCode、Kimi Code；移除接入应用和 vscode。开发页签展示 MCP、CLI、API、SDK，使用相同列表与详情布局。选择不属于当前分组时显示该分组第一项；各分组记住实例内选择。
- Codex、Claude Code、Cursor、OpenClaw、Hermes Agent、OpenCode、pi 的详情增加「境外」标签（英文为 Overseas），原接入方式标签保留。标签按 Agent 产品归属维护，不根据其使用的模型推断部署地区。
- DeerFlow 详情内置 MemoryManager、MCP 两种接入说明，可独立切换与复制；默认 MemoryManager，切换说明后复制反馈重置。新增图标使用本地目录中的原始 Logo，以透明度蒙版保留轮廓并跟随主题文字色，保证暗色可见。
- CLI 详情合并本地 `cli.md` 与 `agent-cli.md`，提供「手动终端安装」「通过 AI Agent 安装」两个内部页签，默认手动安装。页签沿用参考组件的底部边线、24px 间距与主色选中下划线，颜色跟随主题；复制仅包含当前页签正文，切换后反馈重置。
- 元数据与正文从仓库 [本地目录](../../../../../../docs/images/agents) 生成组件内置快照，不在运行时加载远端 JSON 或 Markdown。Codex、Claude Code、TRAE、Cursor、OpenCode、DeepSeek Harness、pi、ZCode、Kimi Code 沿用参考组件的官方安装器完整指令，其他条目使用对应本地文档。更新文档后运行 `node packages/react/src/components/agent-connect/generate-content.mjs`。
- 页面展示脱敏密钥，复制正文使用调用方的原始密钥；替换服务地址与文档中的密钥占位符。密钥不写入 DOM 属性或本地持久化。语言、密钥、地址、分组或条目变化后，复制反馈重置。
- 复制按钮使用 Clipboard API，成功后显示「已复制」并通知 `onCopy`；失败显示「复制失败，请重试」，可再次操作。指令只作为设计展示文本，不执行其中的安装命令。
- 图标来自设计稿导出的 SVG，位于 [图标目录](../../../../icons/svg/agent-connect/README.md)，JS 构建包含图标，无需宿主提供 URL。
- 颜色使用主题基线；暗色跟随宿主 `.dark`。窄容器可横向浏览原始布局，保持设计比例，不改成另一套布局。
- Codex 图标使用透明外底和正常混合模式，保留内部白色符号；详情图标描边使用主题边框，避免在暗色背景上变黑或出现白框。

原来的 `tabs`、`items`、`detail`、`labels` 输入已移除。文案与图标固定是此次设计还原的明确要求。

属性表与可运行示例见[组件文档](../../../../../docs/docs/zh/components/agent-connect.mdx)，命令见[前端工作区 README](../../../../../README.md)。
