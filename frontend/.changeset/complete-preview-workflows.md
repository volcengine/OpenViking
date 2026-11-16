---
'@openviking/react': patch
---

## en-US

Propagate pending Wiki conflicts from deeply nested files to every ancestor directory, including collapsed subtrees, and refresh cleared markers. Preserve conflict tags loaded after an older empty index. Respect write-permission changes in open configuration editors, compare the first version against an empty baseline, and refresh the current version after a restore conflict before allowing another confirmation.

Align compilation settings with directory and Skill pickers, full-path tooltips, frequency radio cards and time selection. Add file selection, historical comparison and file restore to Wiki history in a full-width document panel with the FileViewer diff renderer, hide history for ordinary resource files and while editing, improve pack upload, and keep model names and dashboard tooltips readable.

Keep history loading indicators centred, show initial picker loading immediately, reuse an Arco-derived Switch, keep target paths on one line and hide view switching while editing. Fix the selected background of the history Preview / Markdown tabs.

Complete theme fallbacks on migrated workflow roots, align dark surfaces, schedule selection borders, history badges and diff legends, and apply the console theme to Switch sizes and unavailable states. Preserve the FileViewer's native diff colours. Show the empty state when a selected directory file has no historical versions, including after retrying a failed request.

## zh-CN

Wiki 深层文件的待处理冲突沿祖先目录逐级显示，目录折叠时保留，刷新后清除已解决的标记；旧空索引不覆盖后来加载到的冲突标签。已打开的配置编辑器响应写权限变化，首个版本默认与空内容比较，版本回滚冲突后刷新当前版本，供用户再次确认。

编译配置使用目录和 Skill 选择器，补充完整路径提示、频次单选卡片与时间选择。Wiki 历史使用全宽文档面板和 FileViewer 差异视图，支持文件选择、历史版本对比和文件回滚；普通资源文件及编辑状态隐藏历史入口，优化 pack 上传界面、模型名称换行与看板提示位置。

历史加载状态保留内容高度并显示居中图标，目录和 Skill 选择器打开即进入加载态；查看变更复用迁移自 Arco 的共享 Switch，目标路径保持单行，编辑时隐藏视图切换。修复历史正文「预览 / Markdown」页签的选中背景色。

在迁入流程的根节点补齐主题兜底，对齐暗色背景、更新频次选中边框、历史标记与差异图例，并让 Switch 的尺寸和不可用状态采用控制台主题；差异正文保留 FileViewer 原生配色；目录内文件暂无历史版本时展示空态，加载失败后重试同样正确展示。
