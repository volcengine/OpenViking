---
'@openviking/react': minor
---

Align the ExperienceDashboard with the Volcengine console: dotted board surface, a single sliding tab indicator centred on each title, a shortcut time-range popover, dashed metric dividers, outcome-coloured trajectory rows with copy and open actions, a source-trace disclosure and a test-material card per message. The directory view gains a numbered pager driven by `data.total`/`pageSize`, while the Impact tab pages trajectories by load-more through `hasMore`.

Add the optional `ExperienceTrajectory.outcome` field and the `pageSize` prop. Rename several `ExperienceDashboardLabels` keys and add `day3`, `injectionHint`, `recallHint`, `emptyTimeline`, `testMessage`, `trajectoryDetails`, `loadingMore` and `loadMore`. Fork the console's Pagination into `shared/primitives/pagination` and add `FileLinkIcon`, `CheckIcon` and `CopyIcon`.

This is a breaking change for consumers of the icons entry: `TabImpactIcon`, `TabSourceIcon` and `TabTestIcon` are removed. The console draws those icons only on its standalone experience detail page, never on the dashboard this component renders, so no host needs them for it.

The directory table keeps its file column at least 180px wide and scrolls horizontally below 560px, so a narrow host no longer collapses the name to zero. A trajectory with an empty `name` falls back to the file name in its URI, and its time is formatted for the interface language instead of printing the endpoint's raw timestamp.

Also carry over the console's later refinements: `ExperienceItem.isUpdated` renders an "Updated" tag beside the name (the host decides, the component compares no timestamps), the two metric headers open their explanation on hover or keyboard focus instead of a native `title`, loading sections show a shimmering skeleton with the state also stated as hidden text, the injection rate keeps one decimal so the three metrics stay aligned, and the Impact timeline appends the next page when scrolled within 240px of the bottom while keeping the clickable button. Add the `updatedTag` label.

The outcome distribution bars now have rounded ends. The console draws them square; `--ov-ui-experience-bar-radius` overrides the radius, and `0` restores square ends for a host that wants them.

A trajectory's title is now plain text: the open button at the end of its row owns that action, as in the console, so one row no longer offers the same destination twice. The shared empty and error state's retry is a bordered secondary button instead of primary-coloured text, matching the console and reading as a control in an otherwise empty section; this applies to the Wiki dashboard too.
