---
'@openviking/react': minor
---

Make FileBrowser preview operations native and resource-aware: content copying, original-byte downloads, experience/Compile history, Compile root configuration, directory ovpack import, ovpack export and confirmed file deletion. Apply namespace, ownership, summary-level, read-only and ACL rules before optional boolean `previewActions` visibility flags. Keep custom command lists and toolbar rendering available through `previewSlots`.

Remove host history/ovpack execution callbacks from preview action configuration. Add multipart FormData support to the native client, retryable workflow dialogs and bilingual workflow-label overrides.
