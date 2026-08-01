# 07 — Local end-to-end verification

**What to build:** Proof that the plugin actually works in a live ZCode session, verified against the 7 false-positive/negative scenarios from the adversarial review. Uses sentinel data to distinguish ZCode captures from Claude Code/Codex contamination. This is the gate before opening a PR.

**Blocked by:** 05 (shared installer), 06 (tests)

**Status:** ready-for-agent

- [ ] Hook output schema manually validated — ZCode log shows no validation failures
- [ ] `SessionStart` injects `<openviking-context>` with profile (verified via unique canary string, not stale data)
- [ ] `UserPromptSubmit` recall returns results for a known sentinel query
- [ ] `Stop` capture stores ZCode turns — verified via `ov read` on the `zc-` prefixed session, NOT via shared `ov find`
- [ ] `PreToolUse` denies `viking://` read attempts with correct redirect message
- [ ] MCP server shows connected with tool count > 0 (not just "connected")
- [ ] Debug log written to `~/.openviking/logs/zcode-hooks.log` (not `cc-hooks.log`)
