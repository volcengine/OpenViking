# 03 — Implement hook dispatcher + transcript parser

**What to build:** The core adapter logic — `zcode-hook.mjs` (dispatcher branching on event name) and `zcode-turns.mjs` (pure transcript parser). Four thin shim scripts wire events to the dispatcher. The dispatcher calls the shared runtime's `buildAgentProfile`, `recallForPrompt`, `addAgentMessages`, `commitAgentSession`. This makes the memory pipeline functional: recall injects context, capture stores turns, commit archives them.

**Blocked by:** 02 (plugin scaffold + vendor)

**Status:** ready-for-agent

- [ ] `zcode-hook.mjs` branches on 3 event types: session-start, user-prompt-submit, stop
- [ ] `zcode-turns.mjs` extracts user/assistant turns, strips injected blocks (`<openviking-context>`, `<relevant-memories>`)
- [ ] Client ID is `zcode`, session prefix is `zc-`
- [ ] Output schema emits ONLY ZCode-recognized keys — no `decision: "approve"` Claude-ism
- [ ] 4 shim scripts (session-start, auto-recall, auto-capture) are 3-line env-setters importing the dispatcher
- [ ] Debug log path is `~/.openviking/logs/zcode-hooks.log` (not `cc-hooks.log`)
