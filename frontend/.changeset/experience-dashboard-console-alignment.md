---
'@openviking/react': minor
---

## en-US

Align the ExperienceDashboard with the Volcengine console: dotted board surface, a single sliding tab indicator centred on each title, a shortcut time-range popover, dashed metric dividers, outcome-coloured trajectory rows with copy and open actions, a source-trace disclosure and a test-material card per message. The directory view gains a numbered pager driven by `data.total`/`pageSize`, while the Impact tab pages trajectories by load-more through `hasMore`.

Add the optional `ExperienceTrajectory.outcome` field and the `pageSize` prop. Rename several `ExperienceDashboardLabels` keys and add `day3`, `injectionHint`, `recallHint`, `emptyTimeline`, `testMessage`, `trajectoryDetails`, `loadingMore` and `loadMore`. Fork the console's Pagination into `shared/primitives/pagination` and add `FileLinkIcon`, `CheckIcon` and `CopyIcon`.

This is a breaking change for consumers of the icons entry: `TabImpactIcon`, `TabSourceIcon` and `TabTestIcon` are removed. The console draws those icons only on its standalone experience detail page, never on the dashboard this component renders, so no host needs them for it.

The directory table keeps its file column at least 180px wide and scrolls horizontally below 560px, so a narrow host no longer collapses the name to zero. A trajectory with an empty `name` falls back to the file name in its URI, and its time is formatted for the interface language instead of printing the endpoint's raw timestamp.

Also carry over the console's later refinements: `ExperienceItem.isUpdated` renders an "Updated" tag beside the name (the host decides, the component compares no timestamps), the two metric headers open their explanation on hover or keyboard focus instead of a native `title`, loading sections show a shimmering skeleton with the state also stated as hidden text, the injection rate keeps one decimal so the three metrics stay aligned, and the Impact timeline appends the next page when scrolled within 240px of the bottom while keeping the clickable button. Add the `updatedTag` label.

The outcome distribution bars now have rounded ends. The console draws them square; `--ov-ui-experience-bar-radius` overrides the radius, and `0` restores square ends for a host that wants them.

A trajectory's title is now plain text: the open button at the end of its row owns that action, as in the console, so one row no longer offers the same destination twice. The shared empty and error state's retry is a secondary button instead of primary-coloured text, so it reads as a control in an otherwise empty section; this applies to the Wiki dashboard too.

## zh-CN

将 ExperienceDashboard 与火山引擎控制台对齐：点阵背景、在各标题中央滑动的单一页签指示器、快捷时间范围浮层、虚线指标分隔、按结果着色且支持复制和打开的轨迹行、来源展开区，以及每条消息的测试材料卡片。目录视图使用 `data.total` 和 `pageSize` 实现页码分页，影响页签通过 `hasMore` 加载更多轨迹。

新增可选字段 `ExperienceTrajectory.outcome` 和 `pageSize` 属性。调整部分 `ExperienceDashboardLabels` 键名，并新增 `day3`、`injectionHint`、`recallHint`、`emptyTimeline`、`testMessage`、`trajectoryDetails`、`loadingMore` 和 `loadMore`。新增 `FileLinkIcon`、`CheckIcon` 和 `CopyIcon`。

图标入口存在破坏性变更：移除 `TabImpactIcon`、`TabSourceIcon` 和 `TabTestIcon`，宿主需移除相关导入。这些图标用于控制台独立经验详情页，不用于此看板。

目录表格的文件列最窄为 180px，容器窄于 560px 时横向滚动。轨迹名称为空时使用 URI 中的文件名，时间按界面语言格式化。

同步后续交互改进：`ExperienceItem.isUpdated` 在名称旁展示“已更新”标签，是否更新由宿主判断；两个指标标题通过悬停或键盘聚焦展示说明；加载区域使用骨架屏，并向读屏器提供状态文本；注入率保留一位小数；影响时间线滚动到距底部 240px 内时加载下一页，同时保留可点击的加载按钮。新增 `updatedTag` 文案。

结果分布条改为圆角，宿主可通过 `--ov-ui-experience-bar-radius` 覆盖，设为 `0` 恢复直角。

轨迹标题改为普通文本，打开操作统一由行尾按钮提供。共用空状态与错误状态的重试操作改为次级按钮，Wiki 看板同样适用。
