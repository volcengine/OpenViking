# @openviking/react

## 0.1.12

### Patch Changes

<!-- release-note:en-US -->

- Connect Console HITL question parsing, ordered answers and conversation continuation. Display and copy readable answer summaries while preserving protocol payloads for transport and storage.
- Support per-question field validators, control renderers and custom cards. Preserve input for retry after failed submissions and discard obsolete interactions when reloading or switching sessions.
- Unify HITL labels, help text, error tips and invalid-control icons. Clear local errors when input values change.
- Preserve drafts across HITL Previous/Next navigation and schedule-frequency changes, and submit all steps once.
- Provide themed, keyboard-accessible time selection with scrolling and wheel input, preserving HH:mm values, validation and disabled states. Prevent overlapping hour and minute updates from reverting one another.
- Standalone VikingBotInteraction no longer requires Ark Design. Import its dedicated stylesheet from `@openviking/react/components/viking-bot/interaction/style.css`.
- HITL text requests honor single-line and multiline modes.
- Export ContextPicker for controlled file and directory selection, search, single or independent multiple selection, asynchronous selection restoration and custom footer actions.
- Built-in directory interactions support ancestor expansion, pagination, child-directory creation, host-provided permissions and validation. Custom answers can delegate selection to the host.
- Reuse TanStack Query v5 clients through OpenVikingProvider, with per-endpoint concurrency and identity-scoped directory and search caches supplied by the default SDK runtime. Preserve rows after background refresh failures and invalidate caches after writes.
- Add optional, strongly typed analytics and frontend monitoring through OpenVikingProvider, both disabled by default. Cover interactions, outcomes, viewport exposure, component and request failures, resources, streams and tasks, with instance identification, bounded traces and bilingual integration guides and event catalogues.
- Make FileBrowser directory navigation expand and refresh its target, support root navigation and preserve pagination, expanded branches and cached children during refresh failures. Keep preview-only navigation available through `reveal: false`, and restore the initially loaded L0/L1 tab indicator.
- Keep FileTree trailing actions visible during horizontal scrolling.
- Add independent FileTree dashboard tooltips and row-name titles.
- Remove FileTree row permission badges while retaining preview ACL indicators and permission controls.
- Let FileBrowser hosts override individual experience-dashboard requests.
- Show preview character counts, modification times and localized model notices.
- Save ordinary file edits directly; create annotated snapshots from Version history.
- Align the FileBrowser ACL picker, allow host-selected principal types and restore inheritance through supported gateway updates. Correct effective-grant attribution when inherited permissions exceed direct permissions.
- Preserve theme danger colors on FileBrowser delete actions.
- Improve advanced search with directory scopes and date ranges, retain results until the close animation ends, and preserve selected preview-tab colors on hover.
- Support resource uploads when the Console service provides attachment uploads, and wait for completion before sending. Cancel uploads and clear managed attachments when the chat identity, explicit session or request lifetime changes, while preserving them when the host echoes a newly created session ID.
- Keep quick inputs above the VikingBot composer and focus the editor when its blank area is clicked.
- Refine conversation timelines, code blocks and session actions, and improve narrow command panels.
- Adapt AgentConnect and ScopeSwitcher to narrow containers.
- Unify themed Checkbox, Radio and focus states, and preserve forced-color focus visibility.
- Share illustrated empty and render-error states.
- Unify operation and request errors with dismissible Notifications and local feedback with Toast. Preserve server reasons, drafts, cancellation and retries without duplicate notices.
- Copy full diagnostics, including available error codes, HTTP status and request IDs, and support loading feedback and notice updates.

Migration: mount components in the browser; SSR and hydration paths are no longer supported. HITL text questions now require non-blank input by default; set `required: false` for optional text. Single-question answer shapes are unchanged; multi-step answers use `steps`.

Remove the obsolete `FileTreeLabels.readOnly`, `noAccess` and `traverseOnly` overrides. FileTree trailing content defaults to `rowTrailingPosition: 'viewport-end'`; set `'row-end'` to retain the previous horizontal-scrolling behavior.

[Full changes](https://github.com/volcengine/OpenViking/compare/react@0.1.11...react@0.1.12)

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 接通 Console HITL 的问题解析、顺序作答、答案组装和继续对话。提交结果以可读摘要展示和复制，传输与存储保留协议原文。
- 支持按问题注入字段校验、控件渲染及自定义卡片。提交失败保留输入并允许重试，重载或切换会话时清除失效交互。
- 统一 HITL 标签、说明、错误 tips 和错误控件内的图标颜色，修改输入时清除本地错误。
- HITL 上一步、下一步及切换定时频次时保留草稿，最后一次性提交所有步骤。
- 时间选择支持主题、键盘、滚动列和滚轮操作，保留 HH:mm 值、校验及禁用状态。避免小时与分钟的交叠操作互相覆盖。
- 独立使用 VikingBotInteraction 不再需要 Ark Design，可从 `@openviking/react/components/viking-bot/interaction/style.css` 引入独立交互样式。
- HITL 文本请求按 multiline 配置显示单行或多行输入。
- 导出 ContextPicker，支持受控文件与目录选择、搜索、单选与独立多选、异步选中回显及自定义底部操作。
- 内置目录交互支持展开祖先、分页、新建子目录及宿主提供的权限和校验，自定义答案可将选择交给宿主。
- OpenVikingProvider 支持复用 TanStack Query v5 客户端、按接口配置并发，以及由 SDK 默认查询运行时提供的身份隔离目录与搜索缓存。后台刷新失败保留行数据，写入后使缓存失效。
- 通过 OpenVikingProvider 新增默认关闭、强类型的埋点与前端监控，覆盖操作、结果、可视曝光、组件与请求异常、资源、流和任务，提供实例标识、有容量限制的链路快照及双语接入指南和事件目录。
- FileBrowser 定位目录时自动展开并刷新目标，支持根目录定位，刷新失败时保留分页、展开分支和缓存子项。保留 `reveal: false` 的仅预览行为，并修复 L0/L1 页签首次加载后的选中指示条。
- FileTree 行尾操作在横向滚动时保持可见。
- FileTree 新增独立的看板图标提示和行名称 title。
- 移除 FileTree 行尾权限标识，保留预览 ACL 标签和权限控制。
- FileBrowser 允许宿主替换经验看板的单项请求。
- 预览页脚展示字符数、更新时间及本地化模型解析提示。
- 普通文件编辑直接保存，带备注的快照在「历史版本」中创建。
- 对齐 FileBrowser 权限选择器，允许宿主指定主体类型，并通过网关支持的更新接口恢复继承。修正继承权限高于直接权限时的有效授权来源。
- FileBrowser 删除操作保留主题警告色。
- 高级搜索支持目录范围与日期筛选，关闭动画结束后再清空结果，预览页签悬停时保留选中背景。
- Console 服务支持附件上传时，对话可添加资源，发送前等待上传完成。聊天身份、显式会话或请求生命周期变化时，取消上传并清空托管附件；宿主回写新建会话 ID 时保留正常上传。
- VikingBot 快捷输入常驻输入框上方，点击输入区空白处聚焦编辑器。
- 调整对话执行时间线、代码块和会话操作，改进窄屏命令面板。
- AgentConnect 和 ScopeSwitcher 适应窄容器。
- 统一跟随主题的 Checkbox、Radio 和焦点状态，保留高对比度焦点提示。
- 共用插画空态与渲染错误状态。
- 操作与请求错误统一使用可关闭的 Notification，本地反馈使用 Toast。保留服务端原因、草稿、取消和重试，避免重复通知。
- 支持复制完整诊断信息及已有的错误码、HTTP 状态码和 Request ID，并支持加载反馈和提示更新。

迁移说明：请在浏览器中挂载组件，不再支持 SSR 与 hydration 路径。HITL 文本题默认要求非空输入，可设置 `required: false` 保留可选文本行为。单题答案结构保持不变，多步骤答案使用 `steps`。

请删除已移除的 `FileTreeLabels.readOnly`、`noAccess` 和 `traverseOnly` 文案覆盖。FileTree 行尾内容默认采用 `rowTrailingPosition: 'viewport-end'`，如需保留原先随整行横向滚动的行为，请设置为 `'row-end'`。

[完整变更](https://github.com/volcengine/OpenViking/compare/react@0.1.11...react@0.1.12)

<!-- /release-note:zh-CN -->

## 0.1.11

### Patch Changes

<!-- release-note:en-US -->

- Add Console mode selection for conversations, commands, or both, plus independent `command.onOpenUri` routing. Track asynchronous tasks across both modes with status snapshots, notifications, and optional query adapters.
- Recover Console inputs after session creation or delivery failures, preserve undelivered questions, and show theme-aware red error notices without storing client errors in conversation history or sending them to the model.
- Open `.abstract.md` and `.overview.md` links at their parent directory's L0 and L1 preview. Fix file-only system directory listings by omitting retrieval tags.
- Align FileBrowser search with the console design, remove the result preview panel, and retain keyboard result navigation. Keep preview bodies mounted across L0/L1/L2 transitions and update the level indicator without flashing the content.
- Render sanitized HTML and recover imported images in Markdown previews. Show a Next action when a directory-guide target is unavailable.
- Refine ExperienceDashboard with Updated tags, accessible metric explanations, loading skeletons, one-decimal injection rates, scroll-based trajectory pagination, sliding tab indicators, and configurable rounded outcome bars. Open trajectories through their row action and use secondary retry buttons in shared dashboard states.
- Isolate rendering failures in blocks, file previews, command results, and conversation regions. Add `slots.errorFallback`, local reset callbacks, and retryable lazy content while preserving healthy regions and drafts.
- Unify FileTree scrollbar colors, themed form controls, and dropdown/select keyboard behavior. Show full quick-command descriptions when summaries or truncation hide them. Add bilingual release notes to the documentation.

Migration: the public `@openviking/react/modules/viking-bot` hook entry is removed; use Console with `modes={['conversation']}` and its conversation services. Remove imports of `TabImpactIcon`, `TabSourceIcon`, and `TabTestIcon`. Remove the obsolete `FileBrowserLabels.searchPreview`, `searchPreviewEmpty`, and `searchPreviewFailed` overrides.

FileBrowser now reads effective permissions from `access` in filesystem `ls`/`stat` responses. Upgrade the backend or adapt custom gateway responses from `permission` to `access`; returning only `permission` no longer supplies the read-only indicator or effective ACL level.

[Full changes](https://github.com/volcengine/OpenViking/compare/react@0.1.10...react@0.1.11)

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- Console 支持选择对话、命令或双模式，并新增独立的 `command.onOpenUri` 路由。两种模式统一跟踪异步任务，提供状态快照、通知和可选的查询适配器。
- 会话创建或消息送达失败后恢复 Console 输入，保留未送达的提问，并显示跟随主题的红色错误提示；客户端错误不保存到会话历史，也不发送给模型。
- 点击 `.abstract.md` 和 `.overview.md` 时定位到父目录的 L0、L1 预览。列出仅包含文件的系统目录时不再请求检索标签。
- FileBrowser 搜索对齐控制台设计，移除结果预览面板，保留键盘导航。L0/L1/L2 切换时保持预览正文挂载，更新层级指示器时避免内容闪烁。
- Markdown 预览支持经过安全清理的 HTML，并恢复导入图片。目录引导目标不可用时显示“下一步”操作。
- ExperienceDashboard 新增“已更新”标签、可访问的指标说明、加载骨架屏、保留一位小数的注入率、滚动加载轨迹、滑动页签指示器及可配置圆角的结果分布条。轨迹统一通过行尾操作打开，共用看板状态使用次级重试按钮。
- 隔离区块、文件预览、命令结果和对话区域的渲染错误。新增 `slots.errorFallback`、局部重置回调和懒加载重试，保留正常区域及草稿。
- 统一 FileTree 滚动条颜色、跟随主题的表单控件和下拉选择的键盘交互。快捷命令配置摘要或描述被截断时展示完整提示。文档站新增双语发布日志。

迁移说明：移除公开的 `@openviking/react/modules/viking-bot` Hook 入口，请改用 `modes={['conversation']}` 的 Console 及其对话服务。请移除 `TabImpactIcon`、`TabSourceIcon`、`TabTestIcon` 导入，以及已废弃的 `FileBrowserLabels.searchPreview`、`searchPreviewEmpty`、`searchPreviewFailed` 文案覆盖。

FileBrowser 改为从文件系统 `ls`/`stat` 响应的 `access` 字段读取有效权限。请升级后端，或在自定义网关中将旧 `permission` 响应适配为 `access`；仅返回 `permission` 将无法提供只读标识或有效 ACL 级别。

[完整变更](https://github.com/volcengine/OpenViking/compare/react@0.1.10...react@0.1.11)

<!-- /release-note:zh-CN -->

## 0.1.10

<!-- release-note:en-US -->

### Patch Changes

- Replace the FilePreview textarea with a lazily loaded CodeMirror editor, including line numbers, undo/redo and keyboard focus outlines. Share Prism syntax highlighting with FileViewer.
- Align ExperienceDashboard with the console: add time-range shortcuts, outcome-colored trajectories, source details, test-material cards and pagination through pageSize and data.total. Add ExperienceTrajectory.outcome and update the dashboard label contract.
- Rename FileTree.renderDashboard to renderRowTrailing and .ov-file-tree__dashboard to .ov-file-tree__trailing. Update the prop and targeted CSS; FileBrowser.renderDashboard is unchanged. The row-end slot now aligns itself to the right.

[Full changes](https://github.com/volcengine/OpenViking/compare/react@0.1.9...react@0.1.10)

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

### Patch Changes

- FilePreview 使用按需加载的 CodeMirror 编辑器替代文本框，支持行号、撤销重做和键盘焦点轮廓，与 FileViewer 共用 Prism 语法高亮。
- ExperienceDashboard 与控制台对齐，新增快捷时间范围、按结果着色的轨迹、来源详情、测试材料卡片，以及通过 pageSize 和 data.total 控制的分页。新增 ExperienceTrajectory.outcome，并调整看板文案接口。
- FileTree.renderDashboard 更名为 renderRowTrailing，.ov-file-tree__dashboard 更名为 .ov-file-tree__trailing，需同步更新属性及相关 CSS；FileBrowser.renderDashboard 保持不变。行尾插槽自动靠右对齐。

[完整变更](https://github.com/volcengine/OpenViking/compare/react@0.1.9...react@0.1.10)

<!-- /release-note:zh-CN -->

## 0.1.9

<!-- release-note:en-US -->

### Patch Changes

- Add public VikingBot chat and session hooks at `@openviking/react/modules/viking-bot` with native streaming, history persistence, and optional interrupt and in-flight follow-up capabilities.
- Display streamed thinking in VikingBot and let Console manage its conversation requests, messages, and editor internally.
- Support host session navigation in Console without interrupting a response when its new ID is reflected in the URL; prevent cancelled requests from restoring old drafts and notify each explicit history selection (including the active session) and New action.
- Use icon-only VikingBot message copy buttons with accessible labels.
- Report conversation persistence errors separately from successful replies.
- Improve FileBrowser URI navigation and add read-only version history and comparison.
- Allow hosts to control the AgentConnect drawer in FileBrowser and FileBrowserActionBar, and keep directory-guide cards aligned during horizontal scrolling.

[Full changes](https://github.com/volcengine/OpenViking/compare/react@0.1.8...react@0.1.9)

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

### Patch Changes

- 新增公开的 VikingBot 对话和会话 Hook，入口为 `@openviking/react/modules/viking-bot`，支持原生流式响应、历史持久化，以及可选的中断和响应中补充消息能力。
- VikingBot 展示流式思考过程，Console 内部管理对话请求、消息和编辑器。
- Console 支持宿主会话导航，将新会话 ID 同步到 URL 时不中断响应；取消请求后不再恢复旧草稿，每次显式选择历史会话（包括当前会话）或新建会话时通知宿主。
- VikingBot 消息复制按钮改为带有可访问名称的图标按钮。
- 会话持久化失败单独提示，保留已经成功的回复。
- 改进 FileBrowser 的 URI 导航，新增只读版本历史与对比。
- 宿主可控制 FileBrowser 和 FileBrowserActionBar 的 AgentConnect 抽屉，目录引导卡片在横向滚动时保持对齐。

[完整变更](https://github.com/volcengine/OpenViking/compare/react@0.1.8...react@0.1.9)

<!-- /release-note:zh-CN -->

## 0.1.8

<!-- release-note:en-US -->

### Patch Changes

- Ship the file tree and preview updates from fork main, including console preview controls.
- Keep authentication validation independent of translated display text.
- Localize experience injection rate formatting.
- Publish source changes as betas based on the next patch after npm's last stable release; keep stable releases manual.
- Add FileBrowser search with find, search, grep and glob modes, directory and date filters, and navigation from search results.
- Rename Tui to CommandConsole and FileBrowserEntries to FileBrowserActionBar, including subpaths, types and styles. Move the six authentication forms under components/auth and stop exporting FilePreview LoadingState.
- Replace the legacy preview menu and host history/ovpack callbacks with FilePreview actions and FileBrowser previewActions/previewSlots. Add native history, compile configuration, ovpack import/export and file deletion.
- Use { name, value } quick-command entries instead of strings and WikiDashboard.defineActions instead of dedicated edit and discussion callbacks.

[Full changes](https://github.com/volcengine/OpenViking/compare/react@0.1.7...react@0.1.8)

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

### Patch Changes

- 更新文件树和预览，包括 Console 预览控制。
- 认证校验不再依赖翻译后的界面文案。
- 经验注入率按界面语言格式化。
- 源码变更自动发布 beta，版本基于 npm 最新稳定版的下一个补丁版本；稳定版仍需手动发布。
- FileBrowser 新增 find、search、grep 和 glob 四种检索模式，支持目录、日期筛选及从结果定位资源。
- Tui 更名为 CommandConsole，FileBrowserEntries 更名为 FileBrowserActionBar，同步调整子路径、类型和样式。六个认证表单移至 components/auth，FilePreview 不再导出 LoadingState。
- 使用 FilePreview actions 和 FileBrowser previewActions/previewSlots 替代旧预览菜单及宿主历史、ovpack 回调，新增原生历史、编译配置、ovpack 导入导出和文件删除。
- 快捷命令从字符串迁移为 { name, value } 对象；WikiDashboard 使用 defineActions 替代专用编辑和讨论回调。

[完整变更](https://github.com/volcengine/OpenViking/compare/react@0.1.7...react@0.1.8)

<!-- /release-note:zh-CN -->

## 0.1.7

<!-- release-note:en-US -->

### Patch Changes

- Add native node permission management to FileBrowser, including principal paging and inherited-permission checks.
- Preserve pending permission confirmations and in-flight saves when locale or equivalent request options change.
- Align file-tree toolbar defaults and shared form controls with the console theme.

[Full changes](https://github.com/volcengine/OpenViking/compare/react@0.1.6...react@0.1.7)

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

### Patch Changes

- FileBrowser 新增原生节点权限管理，支持权限主体分页和继承权限校验。
- 切换语言或传入等价请求配置时，保留待确认的权限操作和进行中的保存。
- 文件树工具栏默认项与共用表单控件对齐控制台主题。

[完整变更](https://github.com/volcengine/OpenViking/compare/react@0.1.6...react@0.1.7)

<!-- /release-note:zh-CN -->

## 0.1.6

<!-- release-note:en-US -->

### Patch Changes

- Add WikiDashboard and ExperienceDashboard with native data loading in FileBrowser.
- Make business-block hooks and scope controllers internal. Consume Console, FileBrowser and ScopeCascade through their composed components.
- Keep internal memory metadata out of file previews and include default scope-level icons.
- Anchor file-tree cards to the visible row edge and improve narrow Tui empty states and result rows.

[Full changes](https://github.com/volcengine/OpenViking/compare/react@0.1.5...react@0.1.6)

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

### Patch Changes

- 新增 WikiDashboard 和 ExperienceDashboard，FileBrowser 通过原生接口加载看板数据。
- 业务区块 Hook 和作用域控制器改为内部实现，宿主通过 Console、FileBrowser 和 ScopeCascade 组合组件接入。
- 文件预览过滤内部记忆元数据，并内置默认的作用域层级图标。
- 文件树浮层对齐可见行边缘，改进窄屏下 Tui 空状态和结果行的显示。

[完整变更](https://github.com/volcengine/OpenViking/compare/react@0.1.5...react@0.1.6)

<!-- /release-note:zh-CN -->

## 0.1.5

<!-- release-note:en-US -->

### Patch Changes

- Make locale optional and inherit it from the nearest block or Provider, falling back to en-US.
- Improve AgentConnect guide rendering and credential controls.

[Full changes](https://github.com/volcengine/OpenViking/compare/react@0.1.4...react@0.1.5)

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

### Patch Changes

- locale 改为可选，继承最近的区块或 Provider，均未配置时默认使用 en-US。
- 改进 AgentConnect 指南渲染和凭证操作。

[完整变更](https://github.com/volcengine/OpenViking/compare/react@0.1.4...react@0.1.5)

<!-- /release-note:zh-CN -->

## 0.1.4

<!-- release-note:en-US -->

### Patch Changes

- Implement Console and VikingBot interactions, including native commands, command history and conversation controls.
- Embed AgentConnect and resource import in FileBrowser. Replace FileBrowser.toolbarActions with agentConnect, onImportResources and renderImportResources.
- Add built-in bilingual copy. Views with built-in text require locale; migrate AgentConnect from zh/en to zh-CN/en-US.
- Add the Tui quick-command empty state. Replace labels.empty with the built-in labels or a custom emptyState.
- Default client fetch requests to credentials=include. Set credentials to same-origin or omit to retain the previous cookie policy.

[Full changes](https://github.com/volcengine/OpenViking/compare/react@0.1.3...react@0.1.4)

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

### Patch Changes

- 实现 Console 和 VikingBot 交互，包括原生命令、命令历史和对话操作。
- FileBrowser 内置 AgentConnect 和资源导入，使用 agentConnect、onImportResources 和 renderImportResources 替代 FileBrowser.toolbarActions。
- 新增内置双语文案。有内置文案的视图要求传入 locale，AgentConnect 的 zh/en 需迁移为 zh-CN/en-US。
- Tui 新增快捷命令空状态，使用内置文案或自定义 emptyState 替代 labels.empty。
- 客户端 fetch 请求默认使用 credentials=include；设置为 same-origin 或 omit 可保留原有 Cookie 策略。

[完整变更](https://github.com/volcengine/OpenViking/compare/react@0.1.3...react@0.1.4)

<!-- /release-note:zh-CN -->

## 0.1.3

<!-- release-note:en-US -->

### Patch Changes

- Implement FileBrowser with native tree loading, resource selection and file and directory previews.
- Support dragging or keyboard controls to resize the tree column, with horizontal scrolling for long filenames.
- Add FileBrowserEntries for connecting agents and importing resources.
- Extend the request client with SSE streaming and binary downloads, preserving server errors and request cancellation.

[Full changes](https://github.com/volcengine/OpenViking/compare/react@0.1.2...react@0.1.3)

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

### Patch Changes

- 实现 FileBrowser，通过原生接口加载文件树、选择资源，以及预览文件和目录。
- 文件树列宽支持拖动和键盘调整，长文件名支持横向滚动。
- 新增 FileBrowserEntries，提供连接 Agent 和导入资源的操作。
- 请求客户端支持 SSE 流式响应和二进制下载，保留服务端错误及请求取消行为。

[完整变更](https://github.com/volcengine/OpenViking/compare/react@0.1.2...react@0.1.3)

<!-- /release-note:zh-CN -->

## 0.1.2

<!-- release-note:en-US -->

### Patch Changes

- Implement FileTree keyboard navigation and file previews for text, documents, images, audio and video.
- Implement the Tui command console and AgentConnect integration guides, and align ScopeSwitcher with the console design.
- Introduce shared --ov-ui-color-* theme variables and dark styling controlled by the host html.dark class.
- Add the VikingBot public interface and placeholder view. Remove the TaskCenter block entry.

[Full changes](https://github.com/volcengine/OpenViking/compare/react@0.1.1...react@0.1.2)

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

### Patch Changes

- 实现 FileTree 键盘导航，以及文本、文档、图片、音频和视频的文件预览。
- 实现 Tui 命令控制台和 AgentConnect 接入指南，ScopeSwitcher 与控制台设计对齐。
- 新增共用的 --ov-ui-color-* 主题变量，暗色样式由宿主的 html.dark 类控制。
- 新增 VikingBot 公开接口和占位视图，移除 TaskCenter 区块入口。

[完整变更](https://github.com/volcengine/OpenViking/compare/react@0.1.1...react@0.1.2)

<!-- /release-note:zh-CN -->

## 0.1.1

<!-- release-note:en-US -->

### Patch Changes

- Establish the public interfaces for file browsing, file preview, the Tui command console, AgentConnect and scope selection, with matching Chinese and English integration documentation.
- Add the OpenViking request client and Provider, independently subscribed scope selectors and ScopeCascade.
- Separate asynchronous file operations from success notifications. Migrate FileTree execution to operations and FilePreview execution to operations and editing; on... callbacks report successful operations.
- Use ov-prefixed BEM classes and narrow root exports. Import specialized types and utilities from the corresponding component or block subpath.

[Full changes](https://github.com/volcengine/OpenViking/compare/b801b6f43dd5d9801e3c29eae9cc1bf216dec8d6...react@0.1.1)

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

### Patch Changes

- 建立文件浏览、文件预览、Tui 命令控制台、AgentConnect 和作用域选择的公开接口，并提供中英文接入文档。
- 新增 OpenViking 请求客户端和 Provider、独立订阅的作用域选择器及 ScopeCascade。
- 将异步文件操作与成功通知分离。FileTree 操作迁至 operations，FilePreview 操作迁至 operations 和 editing，on... 回调用于通知操作成功。
- 样式使用 ov 前缀的 BEM 类名，并收窄根入口导出；专用类型和工具从对应业务组件或区块子路径引入。

[完整变更](https://github.com/volcengine/OpenViking/compare/b801b6f43dd5d9801e3c29eae9cc1bf216dec8d6...react@0.1.1)

<!-- /release-note:zh-CN -->

## 0.1.0

<!-- release-note:en-US -->

### Patch Changes

- Publish the initial React package with StatusBadge, file-type icons, component subpaths and separately imported stylesheets.
- Support React 18 and React 19 through peer dependencies.

[Release source](https://www.npmjs.com/package/@openviking/react/v/0.1.0)

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

### Patch Changes

- 首次发布 React 包，提供 StatusBadge、文件类型图标、业务组件子路径和可独立导入的样式。
- 通过 peer dependencies 支持 React 18 和 React 19。

[发布来源](https://www.npmjs.com/package/@openviking/react/v/0.1.0)

<!-- /release-note:zh-CN -->
