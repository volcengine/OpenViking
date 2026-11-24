# @openviking/react

## 0.2.0

### Minor Changes

<!-- release-note:en-US -->

- 609baba: Let FileBrowser hosts load experience metrics and override individual experience dashboard sections.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 609baba: 支持 FileBrowser 宿主加载经验应用指标，并按区块覆盖经验看板的展示。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- 704bec6: Add optional FileBrowser props for hosts to save and restore expanded folders independently of the selected resource.

  Hosts can also persist explicit collapses across mounts when home alias resolution is temporarily unavailable.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 704bec6: FileBrowser 增加可选属性，支持宿主独立保存和恢复目录展开状态与选中资源。

  主目录别名暂时无法解析时，宿主也可通过完整快照跨挂载保存手动折叠记录。

<!-- /release-note:zh-CN -->

### Patch Changes

<!-- release-note:en-US -->

- 7a15a83: Make the AgentConnect full-instruction copy button more prominent and clarify its labels. Allow the instruction header and copy button to wrap in narrow containers regardless of component stylesheet import order. Unify rounded, theme-aware instruction cards, code blocks and tables, and keep a single table border even when only a header is present.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 7a15a83: 突出 AgentConnect 完整指令复制按钮并明确其文案，支持说明标题栏和复制按钮在窄容器内换行，不受组件样式引入顺序影响。统一说明卡片、代码块和表格的圆角及主题背景，仅有表头的表格也保持单层边框。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- 9dd10ec: Consolidate compile help under one Console entry and document the model and schedule fields in `--args`.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 9dd10ec: 将编译指引集中到终端的一个入口，并补充 `--args` 的模型与定时字段说明。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- 8a52885: Support session auto-creation, memory policies, context token budgets, and retained message counts in Console. Send structured message parts, creation timestamps, and batch JSON. Session write commands accept boolean or JSON object telemetry options and display the server's telemetry summary with a label in the current Console language.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 8a52885: Console 支持自动创建会话、配置记忆策略、设置上下文 Token 预算和保留消息数，并支持发送结构化消息片段、指定创建时间和批量写入 JSON 消息。会话写入命令接受布尔值或 JSON 对象类型的遥测参数，并展示服务端返回的遥测摘要，标签随 Console 语言切换。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- 4c11e48: Console can restore read-only command history after a refresh in the same browser tab when the host provides a scoped `commandHistoryKey`.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 4c11e48: 宿主提供按身份范围隔离的 `commandHistoryKey` 后，Console 可在同一浏览器标签页刷新时恢复只读命令历史。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- 9f03f45: Add Compile create before Compile in the Console quick-start actions and fill in the command syntax for both. Clarify the compile configuration and directory parameters in the built-in help, and keep parameter descriptions readable in narrow popovers.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 9f03f45: 在 Console 快速体验中将 compile create 放在 compile 前面，点击可填入对应的命令模板。补充终端帮助中的编译配置和目录参数说明，并避免窄浮窗将说明列挤得过窄。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- 989e6c7: Show the error message directly when a command fails. If the API error message is blank, show its error code.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 989e6c7: 命令执行失败时，结果正文直接展示错误消息。接口返回的错误消息为空时，展示错误码。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- 9f03f45: Keep Console command history in local storage for each configured library and user scope. The history menu and arrow-key recall restore the same submitted commands after a reload, including asynchronous resource imports.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 9f03f45: 按配置的库和用户作用域在本地存储中保存 Console 命令历史。刷新后，历史菜单和方向键可回查同一份已提交的命令，包括异步资源导入命令。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- 61a0546: Keep resource URIs with spaces clickable in Console result URI fields and quoted URI arguments. In plain text, Markdown code, and free-text command arguments, link each URI token separately.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 61a0546: 终端结果的 URI 字段和带引号的 URI 参数会为含空格的资源路径保留完整链接。普通文本、Markdown 代码和自由文本参数按 URI 逐项生成链接。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- 8a52885: Keep the Deleted badge on confirmed file deletions in the history and comparison picker. The file must exist in the first parent. Older servers can confirm deletion through snapshot diff. Compare with an earlier version to see the removed content. Missing snapshot content for a tracked file appears as a read error.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 8a52885: 确认文件删除的版本会在文件历史和对比下拉框中保留「已删除」标记。该路径在首父提交中必须是文件，旧版服务端也可以通过快照差异确认删除。与更早的版本对比可查看删除的内容。仍在快照中跟踪的文件如果缺失存储内容，则显示读取错误。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- 8a52885: Disable dates outside the selected search range and reject reversed ranges entered by hand.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 8a52885: 置灰会导致范围倒序的日期，并阻止手动输入倒序范围。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- 442638c: Restore `compile create` as a separate Console entry before `compile`, split their guidance, and align creation parameters with the latest product copy.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 442638c: 恢复独立的 `compile create` 终端入口并放在 `compile` 前，拆分两条命令的指引，按最新产品文案补齐创建参数。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- 94b18d3: Switch retrieval modes at the top of the FileBrowser results area, including when the result count is zero. Switching modes retains the query and compatible filters.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 94b18d3: 文件浏览器在结果区顶部提供检索方式切换入口，检索到 0 条结果时也会显示条数并支持切换。切换时保留输入词和新模式支持的筛选。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- 8a52885: Show the resource name on the preview tab immediately when opening a Wiki compilation or conflict dashboard.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 8a52885: 点击 Wiki 编译或冲突图标时，预览标签立即显示文件名或目录名。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- c16c4cc: VikingBot now uses dedicated controls for Wiki and scheduled update questions, supports submitting the current session for memory extraction, and presents rename and delete in a menu on each history entry.

  Console can also show a Bot activation screen and open the conversation when a host confirms the current library's Bot is ready.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- c16c4cc: VikingBot 为创建 Wiki 和配置定时更新提供专用澄清控件，支持提交当前会话以提取记忆，并在历史记录每一项的菜单中提供重命名和删除。

  Console 也可展示 Bot 启用页，并在宿主确认当前库的 Bot 就绪后打开对话。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- 704bec6: Return focus to the VikingBot session history button when a session dialog closes and focus would otherwise fall back to the page. The tooltip stays hidden after mouse dismissal and remains available on hover and keyboard focus.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 704bec6: VikingBot 会话操作弹窗关闭后，如果焦点落到页面主体，会将焦点恢复到历史会话按钮。用鼠标关闭弹窗时保持提示隐藏，鼠标悬停和键盘聚焦时仍会显示提示。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- 8a52885: Keep Wiki conflict highlights visible while any part of a highlighted block remains. Open the selected conflict in the editor, close conflict tooltips when scrolling or leaving the editor, and show a success notice after saving a Wiki file.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 8a52885: 编辑 Wiki 冲突时，高亮保留到整段内容被删除。点击冲突的铅笔会定位到该条冲突，滚动编辑器或焦点离开时收起冲突说明。Wiki 文件保存成功后，右上角显示成功提示。

<!-- /release-note:zh-CN -->

## 0.1.14

### Minor Changes

<!-- release-note:en-US -->

- 128b63d: AgentConnect accepts `isEnterprise` to show English or Chinese team onboarding instructions for agents supported by the official installer, preserving API key masking and copying. Existing instructions remain the default.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 128b63d: AgentConnect 新增 `isEnterprise`，为支持官方安装器的 Agent 展示中英文团队接入文案，复用 API Key 脱敏和复制逻辑。默认继续使用原有文案。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- 196fad5: Add OpenViking CLI session, file download, and OVPack commands to Console. Local inputs use the browser file picker. Administrator-only backup and restore require opting in through `commandOptions.allowAdminCommands`.

  With `--read-content`, Console search results display multiline file content and fall back to the abstract when content is unavailable or blank. Search result items support an optional `content` field for the multiline body.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 196fad5: Console 新增 OpenViking CLI 会话、文件下载和 OVPack 命令。本地输入通过浏览器文件选择器提供。管理员备份和恢复需要通过 `commandOptions.allowAdminCommands` 显式启用。

  Console 检索命令使用 `--read-content` 时展示多行正文，正文不可读或为空白时回退到摘要。检索结果项支持通过可选的 `content` 字段提供多行正文。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- 81c42ac: Align the Console's built-in command options with the Volcengine terminal, including filesystem limits, search filters, ACL JSON, inline writes, resource import options, and task filters. Show the canonical names, aliases, and defaults in command help. `tree` now requires a directory URI, for example `ov tree viking://resources -L 2`.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 81c42ac: 对齐 Console 内置命令与火山引擎终端的参数，包括文件系统限制、检索过滤、ACL JSON、正文写入、资源导入和任务筛选。在命令帮助中展示规范参数名、别名和默认值。`tree` 现在需要传入目录 URI，例如 `ov tree viking://resources -L 2`。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- d510934: FileBrowser can show test materials, group source trajectories by update, and display trajectory documents and source messages when the host supplies the corresponding loaders.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- d510934: 宿主提供相应的数据源后，文件浏览区块可分页展示测试素材，按经验更新分组展示来源轨迹，并在详情中查看轨迹正文和来源消息。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- a8d37f3: Show timestamped transcripts and extracted frames in FileBrowser for processed video segments. Select a transcript entry to seek the video, or open an extracted frame at full size.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- a8d37f3: FileBrowser 支持展示已解析视频切片的带时间戳转录文稿和抽帧图片。点击文稿可跳转到对应播放位置，点击抽帧可放大图片。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- b90eb2e: Show Create Wiki and Scheduled Report shortcuts in managed VikingBot. Use the toolbar @ or type @ in the draft to browse and search for files or directories to include with a message.

  After switching languages, select another shortcut to replace the previous preset draft.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- b90eb2e: 内置 VikingBot 新增「创建 Wiki」和「定时日报」快捷入口；可点击工具栏的 @ 或在输入框键入 @ 浏览和搜索文件或目录，并随消息发送引用。

  切换语言后，点击快捷入口仍可替换之前填入的预设草稿。

<!-- /release-note:zh-CN -->

### Patch Changes

<!-- release-note:en-US -->

- 0019eb7: Keep the active video transcript entry highlighted and in view during playback. Show extracted frame filenames, keep thumbnails stable when resizing the preview, and expand the video and transcript together while preserving playback.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 0019eb7: 播放视频时高亮当前转录文稿并自动滚动。显示抽帧文件名，拖动预览分栏时保持缩略图尺寸稳定，并支持在弹窗中同步查看视频和文稿，保留播放进度。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- b62b243: The command console accepts Han characters in compile configuration names, up to 32 characters in total. ASCII-only names allow up to 64 characters.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- b62b243: 命令台支持使用汉字创建和运行编译配置，含汉字的名称总长最多 32 个字符，纯 ASCII 名称最多 64 个字符。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- a961625: Allow Console hosts to control selected directory references while keeping the built-in picker and search. Reset the selection when switching or starting a conversation. Chat adapters can acknowledge accepted input before the reply arrives so a submitted clarification card can close promptly.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- a961625: 允许宿主控制 Console 的目录引用选中项，同时保留内置目录树和搜索。切换或新建会话时清空引用。对话适配器可以在回复到达前确认输入已送达，让已提交的澄清卡及时关闭。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- d510934: FileBrowser shows the appropriate empty state when a directory has no L0 or L1 summary.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- d510934: 文件浏览区块对未生成摘要的目录显示对应的 L0 和 L1 空态。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- 1c25783: Align the version history and copy icons in the FileBrowser editor with the console. Hide the dashboard tab during editing, show a folder-specific empty message, and label the shared resources root.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 1c25783: 对齐文件浏览区块编辑态的版本历史和复制图标，编辑时隐藏看板页签。展开空文件夹时显示专用提示，并为顶层资源目录添加共享标签。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- 84e5f15: Keep retrieval mode selection beside the FileBrowser search field, including when there are no matches. Switching modes retains the query and compatible filters.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 84e5f15: 文件浏览器在搜索框旁常驻检索方式选择器，无匹配结果时也可以切换。切换时保留输入词和新模式支持的筛选。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- 3e229d1: Prevent the copy-path tooltip from opening after a mouse-driven rollback when focus moves to the copy button. The tooltip still opens on hover or keyboard focus.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 3e229d1: 修复鼠标确认回滚后，焦点落到复制路径按钮时误弹提示的问题。悬停和键盘聚焦仍会显示提示。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- b90eb2e: Make selected text stand out in the file editor, including on Wiki conflict lines.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- b90eb2e: 调整文件编辑器的文字选区颜色，使选区在 Wiki 冲突行上也清楚可见。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- 85b3666: Hide in-progress resource import notifications in Console. Import progress stays visible in the command result, and Console still shows completion and failure notifications.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 85b3666: 隐藏 Console 中进行中的资源导入通知。命令结果仍显示导入进度，Console 仍会显示任务完成和失败通知。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- 85b3666: Reveal the first conflict when opening the Wiki editor.

- 85b3666: Align the VikingBot reference picker with the file tree's root order.

- 85b3666: Close a submitted clarification when VikingBot resumes.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 85b3666: 打开 Wiki 编辑器时定位首个冲突。

- 85b3666: 统一 VikingBot 引用选择器与文件树的根目录顺序。

- 85b3666: VikingBot 继续运行后关闭已提交的澄清弹窗。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- b90eb2e: Enable the Load more and Retry buttons in the VikingBot reference picker so users can continue browsing directory pages and recover from listing errors.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- b90eb2e: 修复 VikingBot 引用选择器的「加载更多」和「重试」按钮，支持继续浏览目录分页并在目录加载失败后重试。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- 13282ba: Show Rename and Delete in a per-session menu in VikingBot history.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 13282ba: VikingBot 历史会话将重命名和删除收拢到每行的更多操作菜单。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- 85b3666: Keep the VikingBot history popover at a stable height during refresh, and show a centered diamond loader when switching conversations. Add session rename and delete actions when the session service supports them.

  Dismiss pending rename and delete confirmations when the session account or service changes. Standalone VikingBot hosts can provide `sessions.scopeKey` to identify their session scope.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 85b3666: 刷新时保持 VikingBot 历史会话弹层高度稳定，切换会话时居中显示菱形加载动画。会话服务支持相应操作时，提供会话改名和删除入口。

  切换会话账号或服务时关闭已打开的重命名和删除确认框。独立使用 VikingBot 时，宿主可通过 `sessions.scopeKey` 标识会话作用域。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- b90eb2e: Show a progress notification while FileBrowser saves Wiki changes, including conflict resolutions.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- b90eb2e: FileBrowser 保存 Wiki 改动时显示进度通知，包括冲突解决后的保存。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- 1c25783: Highlight unresolved Wiki conflicts in the FileBrowser editor and show their explanations on hover. Conflict metadata stays out of the draft.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 1c25783: 在文件浏览区块的编辑器中高亮未解决的 Wiki 冲突，悬停时显示冲突说明。冲突元数据仍与草稿正文分离。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- 6e8578d: Show the original Wiki conflict trailer in green at the end of the editor. The trailer is read-only, and saving still submits the editable body.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 6e8578d: 在 Wiki 编辑器末尾以绿色显示原始冲突尾注。尾注只读，保存继续提交可编辑正文。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- a86d3a4: Align Markdown and YAML syntax colors and the Wiki conflict tooltip with the console editor. Load YAML front matter highlighting regardless of when the Markdown grammar is registered.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- a86d3a4: 将 Markdown、YAML 语法配色及 Wiki 冲突气泡与控制台编辑器对齐。Markdown 语法提前注册时也能高亮 YAML 文件头。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- b90eb2e: Label versions created by a Wiki rollback and show a confirmation after restoring a version or selecting identical content.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- b90eb2e: 在版本历史中标记 Wiki 回滚生成的版本，并提示回滚完成或所选内容与当前文件一致。

<!-- /release-note:zh-CN -->

## 0.1.13

### Patch Changes

<!-- release-note:en-US -->

- ae0ce7a: Add the named compile workflow and its CLI guide. FileBrowser now reports selected tree entries so hosts can add directory references to VikingBot, whose send button uses a rounded square.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- ae0ce7a: 新增按名称创建和执行编译的流程及 CLI 指引。FileBrowser 支持上报选中的目录树条目，宿主可将目录引用传给 VikingBot，发送按钮采用圆角方形。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- 62d5cd5: Support switching named compilation configurations through directory-style tabs, native Git snapshot history, and Wiki edits with explicit snapshots.

  Propagate pending Wiki conflicts from deeply nested files to every ancestor directory, including collapsed subtrees, and refresh cleared markers. Preserve conflict tags loaded after an older empty index, and let refreshed conflict results override historical compilation counts. Skip inaccessible directory placeholders while traversing containers with readable descendants. Respect write-permission changes in open configuration editors, compare the first version against an empty baseline, and refresh the current version after a restore conflict before allowing another confirmation.

  Align compilation settings with directory and Skill pickers, full-path tooltips, frequency radio cards and time selection. Add file selection, historical comparison and file restore to Wiki history in a full-width document panel with the FileViewer diff renderer, hide history for ordinary resource files and while editing, improve pack upload, and keep model names and dashboard tooltips readable.

  Keep history loading indicators centred, show initial picker loading immediately, reuse an Arco-derived Switch, keep target paths on one line and hide view switching while editing. Fix the selected background of the history Preview / Markdown tabs.

  Complete theme fallbacks on migrated workflow roots, align dark surfaces, schedule selection borders, history badges and diff legends, and apply the console theme to Switch sizes and unavailable states. Preserve the FileViewer's native diff colours. Show the empty state when a selected directory file has no historical versions, including after retrying a failed request.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 62d5cd5: 支持通过目录风格的页签切换编译配置、原生 Git 快照历史，以及显式保留 Wiki 编辑快照。

  Wiki 深层文件的待处理冲突沿祖先目录逐级显示，目录折叠时保留，刷新后清除已解决的标记；旧空索引不覆盖后来加载到的冲突标签，刷新结果优先于历史编译统计；跳过无权限目录占位，保留可展开目录的可读后代遍历。已打开的配置编辑器响应写权限变化，首个版本默认与空内容比较，版本回滚冲突后刷新当前版本，供用户再次确认。

  编译配置使用目录和 Skill 选择器，补充完整路径提示、频次单选卡片与时间选择。Wiki 历史使用全宽文档面板和 FileViewer 差异视图，支持文件选择、历史版本对比和文件回滚；普通资源文件及编辑状态隐藏历史入口，优化 pack 上传界面、模型名称换行与看板提示位置。

  历史加载状态保留内容高度并显示居中图标，目录和 Skill 选择器打开即进入加载态；查看变更复用迁移自 Arco 的共享 Switch，目标路径保持单行，编辑时隐藏视图切换。修复历史正文「预览 / Markdown」页签的选中背景色。

  在迁入流程的根节点补齐主题兜底，对齐暗色背景、更新频次选中边框、历史标记与差异图例，并让 Switch 的尺寸和不可用状态采用控制台主题；差异正文保留 FileViewer 原生配色；目录内文件暂无历史版本时展示空态，加载失败后重试同样正确展示。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- a08cec9: Fix Console showing a command-unavailable warning during execution. CommandConsole now accepts `busy` to lock input and submission while pending result cards show progress; `disabled` retains its unavailable message.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- a08cec9: 修复 Console 执行命令时误显示不可用提示的问题。CommandConsole 新增 `busy`，在等待结果时锁定输入与提交，由执行中的结果卡片展示进度；`disabled` 保留不可用提示。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- 63da276: Align the typography and colors of CommandConsole and VikingBot with the console design. Simplify completed resource import cards and add a result action to CLI completion notifications.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 63da276: 对齐 CommandConsole 和 VikingBot 与控制台的字体及配色，精简资源导入完成卡片，并在 CLI 完成通知中添加查看结果入口。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- 63da276: Updating a mounted `FileBrowser`'s controlled `uri` opens the preview and reveals the resource in the directory tree. Hosts that need a preview-only update can call `openResource(uri, { reveal: false })`. Use `defaultExpandedUri` to set the initial tree expansion.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 63da276: 更新已挂载的 `FileBrowser` 的受控 `uri` 时，区块会打开预览并在目录树中定位资源。需要仅更新预览的宿主可调用 `openResource(uri, { reveal: false })`。用 `defaultExpandedUri` 设置初始展开位置。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- a4c0257: Allow FileBrowser hosts to override individual experience dashboard requests while retaining the built-in UI, pagination, retries, and cancellation.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- a4c0257: FileBrowser 支持由宿主替换经验看板的单项请求，并沿用内置界面、分页、重试和请求取消。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- 0a7197c: After saving an editable ordinary file in FileBrowser, choose whether to keep a snapshot, then enter a description and save it. Snapshot failures retain the description for retry and do not undo the file save. Wiki compile files retain their automatic version history.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 0a7197c: FileBrowser 保存普通可编辑文件后，可选择是否保留快照，再填写描述并保存。快照失败会保留描述以供重试，不影响已保存的文件；Wiki 编译文件沿用自动版本历史。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- 41108b0: Hide the Preview / Source switch during editing. FileBrowser keeps version history available and copies the current editor draft.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 41108b0: 编辑态隐藏「预览 / 源码」切换。文件浏览区块保留版本历史，复制正文时取当前编辑草稿。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- 62d5cd5: Use the same theme background for the file tree panel, FileBrowser directory pane and sticky trailing actions. Keep normal, hover and selected surfaces consistent in light and dark themes without host-specific overrides, while preserving text masking during horizontal scrolling.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 62d5cd5: 统一文件树面板、FileBrowser 目录面板与行尾吸附操作区的主题背景，浅色、深色、悬停和选中态保持一致，无需宿主额外适配，并保留横向滚动时对文字的遮挡。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- 41108b0: Replace the file path in Wiki conflict rows with a compact pencil button. File Browser opens the editor directly for pending conflicts when editing is allowed.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 41108b0: Wiki 冲突列表将文件路径替换为铅笔图标按钮。有编辑权限时，可从文件浏览器的待确认冲突直接进入编辑。

<!-- /release-note:zh-CN -->
<!-- release-note:en-US -->

- 41108b0: Use tagged data-plane search to list Wiki conflicts and mark directories with pending conflicts, even when the matching files are collapsed. Refresh conflict status after saving a Wiki file. Accept the current and legacy Compile registry response fields.

<!-- /release-note:en-US -->

<!-- release-note:zh-CN -->

- 41108b0: 通过数据面标签检索列出 Wiki 冲突，即使冲突文件所在分支处于收起状态，也能标记待确认冲突目录。保存 Wiki 文件后更新冲突状态，同时兼容当前和旧版的编译配置响应字段。

<!-- /release-note:zh-CN -->

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
