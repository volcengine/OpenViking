# 02 — Create plugin scaffold + vendor shared runtime

**What to build:** A self-contained `examples/zcode-memory-plugin/` directory with `.zcode-plugin/plugin.json`, `.mcp.json`, `hooks/hooks.json` (4 events), and the vendored `scripts/shared/*.mjs` produced by `sync.mjs`. The plugin can be loaded by ZCode's plugin marketplace and shows up in Settings → Plugin Management, even though the hook scripts are stubs at this point. This is the structural skeleton — everything subsequent tickets plug into.

**Blocked by:** None — can start immediately (parallel with 01).

**Status:** ready-for-agent

- [ ] `examples/zcode-memory-plugin/` exists with `.zcode-plugin/plugin.json`, `.mcp.json`, `hooks/hooks.json`
- [ ] `hooks.json` contains ONLY `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `Stop` — no unsupported events
- [ ] `sync.mjs` TARGETS array updated with zcode entry + `sync.test.mjs` mirrored
- [ ] `node examples/memory-plugin-shared/sync.mjs` runs clean and produces vendored `scripts/shared/*.mjs`
- [ ] Plugin loads in ZCode (visible in Settings → Plugin Management, manifest not rejected)
