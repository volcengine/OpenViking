# OpenClaw ContextEngine Plugin (OpenViking) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build `examples/openclaw-contextengine-plugin` to provide full RFC-scoped OpenClaw context-engine integration with OpenViking (session-start injection, per-turn retrieval, compact batch ingestion, active tools, skill/tool memory augmentation, and CLI guidance).

**Architecture:** Implement a single plugin with dual role: register `kind: "context-engine"` via `registerContextEngine`, and register supporting hooks/tools via plugin API. Keep context lifecycle logic in `context-engine.ts`, retrieval/ingestion/injection in focused modules, and make all advanced behavior configurable with safe defaults and graceful degradation.

**Tech Stack:** TypeScript (ESM), OpenClaw plugin SDK/context-engine interfaces, Vitest, OpenViking HTTP API.

---

## Implementation Rules

- Use @superpowers:test-driven-development for every implementation task.
- Keep diffs minimal and scoped (DRY, YAGNI).
- Commit after each task (small, atomic commits).
- Prefer unit tests + narrow integration tests before broad end-to-end checks.

---

### Task 1: Scaffold Plugin Package

**Files:**
- Create: `examples/openclaw-contextengine-plugin/package.json`
- Create: `examples/openclaw-contextengine-plugin/tsconfig.json`
- Create: `examples/openclaw-contextengine-plugin/openclaw.plugin.json`
- Create: `examples/openclaw-contextengine-plugin/index.ts`
- Create: `examples/openclaw-contextengine-plugin/types.ts`
- Test: `examples/openclaw-contextengine-plugin/index.test.ts`

**Step 1: Write the failing test**

```ts
import { describe, it, expect } from "vitest";
import plugin from "./index.js";

describe("plugin scaffold", () => {
  it("exports contextengine-openviking plugin id", () => {
    expect(plugin.id).toBe("contextengine-openviking");
    expect(plugin.kind).toBe("context-engine");
  });
});
```

**Step 2: Run test to verify it fails**

Run: `pnpm vitest examples/openclaw-contextengine-plugin/index.test.ts`
Expected: FAIL with missing files/exports.

**Step 3: Write minimal implementation**

```ts
const plugin = {
  id: "contextengine-openviking",
  kind: "context-engine" as const,
  register() {},
};
export default plugin;
```

Plus minimal `openclaw.plugin.json` with `id`, `kind`, `configSchema`.

**Step 4: Run test to verify it passes**

Run: `pnpm vitest examples/openclaw-contextengine-plugin/index.test.ts`
Expected: PASS.

**Step 5: Commit**

```bash
git add examples/openclaw-contextengine-plugin/package.json examples/openclaw-contextengine-plugin/tsconfig.json examples/openclaw-contextengine-plugin/openclaw.plugin.json examples/openclaw-contextengine-plugin/index.ts examples/openclaw-contextengine-plugin/types.ts examples/openclaw-contextengine-plugin/index.test.ts
git commit -m "feat(contextengine-openviking): scaffold plugin package"
```

---

### Task 2: Implement Config Parsing + Manifest Schema Alignment

**Files:**
- Create: `examples/openclaw-contextengine-plugin/config.ts`
- Modify: `examples/openclaw-contextengine-plugin/openclaw.plugin.json`
- Test: `examples/openclaw-contextengine-plugin/config.test.ts`

**Step 1: Write the failing test**

```ts
import { describe, it, expect } from "vitest";
import { parseConfig } from "./config.js";

describe("parseConfig", () => {
  it("applies defaults", () => {
    const cfg = parseConfig({});
    expect(cfg.mode).toBe("local");
    expect(cfg.retrieval.enabled).toBe(true);
    expect(cfg.ingestion.writeMode).toBe("compact_batch");
  });
});
```

**Step 2: Run test to verify it fails**

Run: `pnpm vitest examples/openclaw-contextengine-plugin/config.test.ts`
Expected: FAIL (`parseConfig` missing).

**Step 3: Write minimal implementation**

Implement `parseConfig(value)` with:
- strict allowed keys
- defaults from design doc
- number clamping
- enum validation for `mode`, `retrieval.injectMode`, `ingestion.writeMode`

**Step 4: Run test to verify it passes**

Run: `pnpm vitest examples/openclaw-contextengine-plugin/config.test.ts`
Expected: PASS.

**Step 5: Commit**

```bash
git add examples/openclaw-contextengine-plugin/config.ts examples/openclaw-contextengine-plugin/config.test.ts examples/openclaw-contextengine-plugin/openclaw.plugin.json
git commit -m "feat(contextengine-openviking): add config parser and schema defaults"
```

---

### Task 3: Build OpenViking HTTP Client Wrapper

**Files:**
- Create: `examples/openclaw-contextengine-plugin/client.ts`
- Test: `examples/openclaw-contextengine-plugin/client.test.ts`

**Step 1: Write the failing test**

```ts
import { describe, it, expect } from "vitest";
import { createOpenVikingClient } from "./client.js";

describe("OpenViking client", () => {
  it("normalizes base URL without trailing slash", () => {
    const c = createOpenVikingClient({ baseUrl: "http://127.0.0.1:1933/", timeoutMs: 15000, apiKey: "" });
    expect(c.baseUrl).toBe("http://127.0.0.1:1933");
  });
});
```

**Step 2: Run test to verify it fails**

Run: `pnpm vitest examples/openclaw-contextengine-plugin/client.test.ts`
Expected: FAIL (missing client).

**Step 3: Write minimal implementation**

Implement methods:
- `health()`
- `find(query, opts)`
- `createSession()`
- `addSessionMessage(sessionId, role, content)`
- `commitSession(sessionId)`
- `deleteSession(sessionId)`

**Step 4: Run test to verify it passes**

Run: `pnpm vitest examples/openclaw-contextengine-plugin/client.test.ts`
Expected: PASS.

**Step 5: Commit**

```bash
git add examples/openclaw-contextengine-plugin/client.ts examples/openclaw-contextengine-plugin/client.test.ts
git commit -m "feat(contextengine-openviking): add OpenViking HTTP client wrapper"
```

---

### Task 4: Implement Retrieval Pipeline

**Files:**
- Create: `examples/openclaw-contextengine-plugin/retrieval.ts`
- Test: `examples/openclaw-contextengine-plugin/retrieval.test.ts`

**Step 1: Write the failing test**

```ts
import { describe, it, expect } from "vitest";
import { buildTurnQuery, filterAndRank } from "./retrieval.js";

describe("retrieval", () => {
  it("uses last N user turns to build query", () => {
    const q = buildTurnQuery(
      [
        { role: "user", content: [{ type: "text", text: "A" }] },
        { role: "assistant", content: [{ type: "text", text: "B" }] },
        { role: "user", content: [{ type: "text", text: "C" }] },
      ] as any,
      2,
    );
    expect(q).toContain("A");
    expect(q).toContain("C");
  });
});
```

**Step 2: Run test to verify it fails**

Run: `pnpm vitest examples/openclaw-contextengine-plugin/retrieval.test.ts`
Expected: FAIL.

**Step 3: Write minimal implementation**

Implement:
- query extraction from last N user text turns
- score threshold filtering
- URI dedupe
- top-K limit

**Step 4: Run test to verify it passes**

Run: `pnpm vitest examples/openclaw-contextengine-plugin/retrieval.test.ts`
Expected: PASS.

**Step 5: Commit**

```bash
git add examples/openclaw-contextengine-plugin/retrieval.ts examples/openclaw-contextengine-plugin/retrieval.test.ts
git commit -m "feat(contextengine-openviking): add retrieval query and ranking pipeline"
```

---

### Task 5: Implement Injection Builders

**Files:**
- Create: `examples/openclaw-contextengine-plugin/injection.ts`
- Test: `examples/openclaw-contextengine-plugin/injection.test.ts`

**Step 1: Write the failing test**

```ts
import { describe, it, expect } from "vitest";
import { buildSystemPromptAddition } from "./injection.js";

describe("injection", () => {
  it("builds prompt addition with profile and tool memory", () => {
    const s = buildSystemPromptAddition({ profile: "P", toolMemory: "T", ovCliGuidance: "C" });
    expect(s).toContain("P");
    expect(s).toContain("T");
    expect(s).toContain("C");
  });
});
```

**Step 2: Run test to verify it fails**

Run: `pnpm vitest examples/openclaw-contextengine-plugin/injection.test.ts`
Expected: FAIL.

**Step 3: Write minimal implementation**

Implement:
- `buildSystemPromptAddition(...)`
- `buildSimulatedToolResultInjection(...)`
- max-char truncation helper

**Step 4: Run test to verify it passes**

Run: `pnpm vitest examples/openclaw-contextengine-plugin/injection.test.ts`
Expected: PASS.

**Step 5: Commit**

```bash
git add examples/openclaw-contextengine-plugin/injection.ts examples/openclaw-contextengine-plugin/injection.test.ts
git commit -m "feat(contextengine-openviking): add prompt and simulated result injection builders"
```

---

### Task 6: Implement Ingestion/Compaction Writer

**Files:**
- Create: `examples/openclaw-contextengine-plugin/ingestion.ts`
- Test: `examples/openclaw-contextengine-plugin/ingestion.test.ts`

**Step 1: Write the failing test**

```ts
import { describe, it, expect } from "vitest";
import { toBatchPayload } from "./ingestion.js";

describe("ingestion", () => {
  it("keeps user/assistant/system and optional tool blocks", () => {
    const payload = toBatchPayload({ messages: [] as any, includeSystemPrompt: true, includeToolCalls: true, maxBatchMessages: 200 });
    expect(Array.isArray(payload)).toBe(true);
  });
});
```

**Step 2: Run test to verify it fails**

Run: `pnpm vitest examples/openclaw-contextengine-plugin/ingestion.test.ts`
Expected: FAIL.

**Step 3: Write minimal implementation**

Implement:
- extract compact batch payload
- dedupe window support
- `writeBatchAndCommit(client, payload)`

**Step 4: Run test to verify it passes**

Run: `pnpm vitest examples/openclaw-contextengine-plugin/ingestion.test.ts`
Expected: PASS.

**Step 5: Commit**

```bash
git add examples/openclaw-contextengine-plugin/ingestion.ts examples/openclaw-contextengine-plugin/ingestion.test.ts
git commit -m "feat(contextengine-openviking): implement compaction batch ingestion writer"
```

---

### Task 7: Implement Context Engine Lifecycle

**Files:**
- Create: `examples/openclaw-contextengine-plugin/context-engine.ts`
- Modify: `examples/openclaw-contextengine-plugin/index.ts`
- Test: `examples/openclaw-contextengine-plugin/context-engine.test.ts`

**Step 1: Write the failing test**

```ts
import { describe, it, expect } from "vitest";
import { OpenVikingContextEngine } from "./context-engine.js";

describe("OpenVikingContextEngine", () => {
  it("returns systemPromptAddition from assemble", async () => {
    const engine = new OpenVikingContextEngine({} as any);
    const out = await engine.assemble({ sessionId: "s", messages: [] as any, tokenBudget: 10000 });
    expect(out).toHaveProperty("messages");
    expect(out).toHaveProperty("estimatedTokens");
  });
});
```

**Step 2: Run test to verify it fails**

Run: `pnpm vitest examples/openclaw-contextengine-plugin/context-engine.test.ts`
Expected: FAIL.

**Step 3: Write minimal implementation**

Implement in class:
- `bootstrap`
- `assemble` (session-start/profile + per-turn retrieval injection)
- `afterTurn` (lightweight bookkeeping)
- `compact` (batch write path)

Register it from `index.ts` via `api.registerContextEngine("contextengine-openviking", factory)`.

**Step 4: Run test to verify it passes**

Run: `pnpm vitest examples/openclaw-contextengine-plugin/context-engine.test.ts`
Expected: PASS.

**Step 5: Commit**

```bash
git add examples/openclaw-contextengine-plugin/context-engine.ts examples/openclaw-contextengine-plugin/index.ts examples/openclaw-contextengine-plugin/context-engine.test.ts
git commit -m "feat(contextengine-openviking): implement context engine lifecycle"
```

---

### Task 8: Add Active Tools (`commit_memory`, `search_memories`)

**Files:**
- Create: `examples/openclaw-contextengine-plugin/tools.ts`
- Modify: `examples/openclaw-contextengine-plugin/index.ts`
- Test: `examples/openclaw-contextengine-plugin/tools.test.ts`

**Step 1: Write the failing test**

```ts
import { describe, it, expect } from "vitest";
import { createTools } from "./tools.js";

describe("tools", () => {
  it("exposes commit_memory and search_memories", () => {
    const tools = createTools({} as any);
    const names = tools.map((t) => t.name);
    expect(names).toContain("commit_memory");
    expect(names).toContain("search_memories");
  });
});
```

**Step 2: Run test to verify it fails**

Run: `pnpm vitest examples/openclaw-contextengine-plugin/tools.test.ts`
Expected: FAIL.

**Step 3: Write minimal implementation**

Implement both tools and register with `api.registerTool(...)`.

**Step 4: Run test to verify it passes**

Run: `pnpm vitest examples/openclaw-contextengine-plugin/tools.test.ts`
Expected: PASS.

**Step 5: Commit**

```bash
git add examples/openclaw-contextengine-plugin/tools.ts examples/openclaw-contextengine-plugin/index.ts examples/openclaw-contextengine-plugin/tools.test.ts
git commit -m "feat(contextengine-openviking): add active memory tools"
```

---

### Task 9: Add Skill/Tool Memory Enhancer + CLI Guidance

**Files:**
- Create: `examples/openclaw-contextengine-plugin/skill-tool-memory.ts`
- Modify: `examples/openclaw-contextengine-plugin/context-engine.ts`
- Modify: `examples/openclaw-contextengine-plugin/index.ts`
- Test: `examples/openclaw-contextengine-plugin/skill-tool-memory.test.ts`

**Step 1: Write the failing test**

```ts
import { describe, it, expect } from "vitest";
import { buildToolMemoryHints } from "./skill-tool-memory.js";

describe("skill/tool memory", () => {
  it("returns concise hints", () => {
    const txt = buildToolMemoryHints(["Bash", "Read"], "debug");
    expect(typeof txt).toBe("string");
  });
});
```

**Step 2: Run test to verify it fails**

Run: `pnpm vitest examples/openclaw-contextengine-plugin/skill-tool-memory.test.ts`
Expected: FAIL.

**Step 3: Write minimal implementation**

Implement:
- `buildSkillMemoryAugmentation(...)`
- `buildToolMemoryHints(...)`
- CLI guidance builder for `ov` commands with fallback note

**Step 4: Run test to verify it passes**

Run: `pnpm vitest examples/openclaw-contextengine-plugin/skill-tool-memory.test.ts`
Expected: PASS.

**Step 5: Commit**

```bash
git add examples/openclaw-contextengine-plugin/skill-tool-memory.ts examples/openclaw-contextengine-plugin/context-engine.ts examples/openclaw-contextengine-plugin/index.ts examples/openclaw-contextengine-plugin/skill-tool-memory.test.ts
git commit -m "feat(contextengine-openviking): add skill/tool memory and ov CLI guidance"
```

---

### Task 10: Add Telemetry + Fallback + Integration Tests

**Files:**
- Create: `examples/openclaw-contextengine-plugin/telemetry.ts`
- Create: `examples/openclaw-contextengine-plugin/fallback.ts`
- Create: `examples/openclaw-contextengine-plugin/integration.test.ts`
- Modify: `examples/openclaw-contextengine-plugin/context-engine.ts`

**Step 1: Write the failing test**

```ts
import { describe, it, expect } from "vitest";
import { classifyFallback } from "./fallback.js";

describe("fallback", () => {
  it("classifies timeout fallback", () => {
    expect(classifyFallback(new Error("timeout"))).toBe("retrieval_timeout");
  });
});
```

**Step 2: Run test to verify it fails**

Run: `pnpm vitest examples/openclaw-contextengine-plugin/fallback.test.ts`
Expected: FAIL.

**Step 3: Write minimal implementation**

Implement:
- fallback reason classification
- telemetry counters and structured logging
- integration tests for: retrieval failure non-blocking + compact fallback

**Step 4: Run test to verify it passes**

Run: `pnpm vitest examples/openclaw-contextengine-plugin/*.test.ts`
Expected: PASS.

**Step 5: Commit**

```bash
git add examples/openclaw-contextengine-plugin/telemetry.ts examples/openclaw-contextengine-plugin/fallback.ts examples/openclaw-contextengine-plugin/integration.test.ts examples/openclaw-contextengine-plugin/context-engine.ts
git commit -m "feat(contextengine-openviking): add fallback and telemetry coverage"
```

---

### Task 11: Documentation + Installation + Verification Commands

**Files:**
- Create: `examples/openclaw-contextengine-plugin/README.md`
- Create: `examples/openclaw-contextengine-plugin/INSTALL.md`
- Modify: `docs/plans/2026-03-12-openclaw-contextengine-plugin-design.md`

**Step 1: Write the failing test**

Create checklist-style doc verification in test note:
- local mode install steps present
- remote mode install steps present
- slot selection command present

**Step 2: Run verification to ensure docs missing details**

Run: manual review + grep checks
Expected: at least one required command missing initially.

**Step 3: Write minimal implementation**

Include exact setup commands:
- `openclaw config set plugins.enabled true`
- `openclaw config set plugins.slots.contextEngine contextengine-openviking`
- `openclaw gateway`

**Step 4: Run verification to ensure docs complete**

Run: manual checklist and command scan.
Expected: all required commands documented.

**Step 5: Commit**

```bash
git add examples/openclaw-contextengine-plugin/README.md examples/openclaw-contextengine-plugin/INSTALL.md docs/plans/2026-03-12-openclaw-contextengine-plugin-design.md
git commit -m "docs(contextengine-openviking): add install and operation guide"
```

---

### Task 12: Final Verification Gate

**Files:**
- Modify (if needed): files touched in prior tasks

**Step 1: Run full plugin test suite**

Run: `pnpm vitest examples/openclaw-contextengine-plugin/**/*.test.ts`
Expected: PASS.

**Step 2: Run repository checks relevant to TypeScript quality**

Run: `pnpm tsgo`
Expected: PASS (or no new errors from plugin).

**Step 3: Run formatting/lint checks**

Run: `pnpm check`
Expected: PASS (or plugin-specific fixes only).

**Step 4: Fix any failures minimally and re-run**

Run failed command(s) again until green.
Expected: PASS.

**Step 5: Commit final stabilization changes**

```bash
git add <only-needed-files>
git commit -m "chore(contextengine-openviking): finalize verification fixes"
```

---

## Verification Evidence Checklist (Must include in final handoff)

- Selected context-engine slot resolves correctly.
- Session-start profile injection present in system prompt addition.
- Per-turn retrieval injection appears and is capped by limits.
- Compact batch write triggers OpenViking commit.
- `commit_memory` and `search_memories` tools execute successfully.
- OpenViking down scenario degrades gracefully (no conversation hard-fail).

---

## Suggested Commit Order

1. scaffold
2. config
3. client
4. retrieval
5. injection
6. ingestion
7. context-engine
8. tools
9. skill/tool memory + CLI guidance
10. fallback + telemetry
11. docs
12. final verification
