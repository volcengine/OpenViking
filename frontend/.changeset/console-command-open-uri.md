---
'@openviking/react': patch
---

Give Console command mode its own `command.onOpenUri` override, symmetric with `conversation.onOpenUri`. Both modes fall back to the top-level `onOpenUri`, so a host that routes conversation links and terminal links differently no longer has to intercept the shared callback itself.
