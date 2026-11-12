---
'@openviking/react': patch
---

Keep Console's internally managed conversation active when the host reflects a newly created session in its URL, and support host-provided session navigation and metadata. Prevent cancelled requests from restoring an old draft after navigation, and notify the host for each explicit New action even before a remote session exists.
