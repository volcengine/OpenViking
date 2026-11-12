---
'@openviking/react': minor
---

Align the ExperienceDashboard with the Volcengine console: dotted board surface, icon-bearing tabs, a shortcut time-range popover, dashed metric dividers, outcome-coloured trajectory rows with copy and open actions, a source-trace disclosure and a test-material card per message. The directory view gains a numbered pager driven by `data.total`/`pageSize`, while the Impact tab pages trajectories by load-more through `hasMore`.

Add the optional `ExperienceTrajectory.outcome` field and the `pageSize` prop. Rename several `ExperienceDashboardLabels` keys and add `day3`, `injectionHint`, `recallHint`, `emptyTimeline`, `testMessage`, `trajectoryDetails`, `loadingMore` and `loadMore`. Fork the console's Pagination into `shared/primitives/pagination` and add `FileLinkIcon`, `TabImpactIcon`, `TabSourceIcon`, `TabTestIcon`, `CheckIcon` and `CopyIcon`.

The directory table keeps its file column at least 180px wide and scrolls horizontally below 560px, so a narrow host no longer collapses the name to zero. A trajectory with an empty `name` falls back to the file name in its URI, and its time is formatted for the interface language instead of printing the endpoint's raw timestamp.
