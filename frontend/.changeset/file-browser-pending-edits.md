---
'@openviking/react': patch
---

## en-US

Restore folders using the latest saved open or close operation after alias identity recovery, preserve ordered pending edits across mounts, and retire obsolete directory subscriptions after a spelling change.

Preserve a pending reopen when a refreshed listing changes only the trailing slash of a directory URI.

Resume saved descendants on later pages after home identity resolves, and keep suspended account transitions from changing the expansion state of the committed tree.

## zh-CN

别名身份恢复后按最新保存的展开或折叠操作恢复目录，跨挂载保留待确认操作的顺序，并在 URI 写法切换后释放过期目录订阅。

目录列表刷新时仅改变 URI 尾部斜杠，仍保留待确认的重新展开操作。

主目录身份确认后自动恢复后续页的已保存子目录，并防止挂起的账号切换影响当前已显示目录树的展开状态。
