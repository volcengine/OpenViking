# 06 — Write tests (turns parser + hook output schema)

**What to build:** Two `node:test` files that catch the two highest-risk silent-failure modes identified in the adversarial review: (1) transcript parser producing empty turns if ZCode's stdin field names don't match, and (2) hook output containing unrecognized keys that ZCode's strict schema silently discards.

**Blocked by:** 03 (hook dispatcher), 04 (URI guard)

**Status:** ready-for-agent

- [ ] `zcode-turns.test.mjs` covers: prompt+assistant extraction, empty input, tag stripping, state fallback
- [ ] `zcode-hooks.test.mjs` covers: output contains only recognized keys for each event, deny decision uses correct vocabulary, disabled state produces empty output
- [ ] Both files runnable via `node --test` with zero external dependencies
- [ ] All cases pass
