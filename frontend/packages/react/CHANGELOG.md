# @openviking/react

## 0.1.9

### Patch Changes

- Add public VikingBot chat and session hooks at `@openviking/react/modules/viking-bot` with native streaming, history persistence, and optional interrupt and in-flight follow-up capabilities.
- Display streamed thinking in VikingBot and let Console manage its conversation requests, messages, and editor internally.
- Support host session navigation in Console without interrupting a response when its new ID is reflected in the URL; prevent cancelled requests from restoring old drafts and notify each explicit history selection (including the active session) and New action.
- Use icon-only VikingBot message copy buttons with accessible labels.
- Report conversation persistence errors separately from successful replies.
- Improve FileBrowser URI navigation and add read-only version history and comparison.
- Allow hosts to control the AgentConnect drawer in FileBrowser and FileBrowserActionBar, and keep directory-guide cards aligned during horizontal scrolling.

## 0.1.8

### Patch Changes

- Ship the file tree and preview updates from fork main, including console preview controls.
- Keep authentication validation independent of translated display text.
- Localize experience injection rate formatting.
- Publish source changes as betas based on the next patch after npm's last stable release; keep stable releases manual.
