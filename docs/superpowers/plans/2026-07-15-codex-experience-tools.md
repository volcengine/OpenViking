# Codex Experience Memory Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bundle the Experience Memory skill and expose `search_experience` and `read_experience` through the existing OpenViking Codex plugin without additional user configuration.

**Architecture:** Add an optional local-tool adapter extension to the shared stdio MCP proxy core. The Codex plugin supplies an Experience adapter that appends two tools to `tools/list`, handles their `tools/call` requests through OpenViking REST APIs, and forwards every other MCP request unchanged.

**Tech Stack:** Node.js ESM, MCP JSON-RPC, OpenViking REST APIs, Node `test`, Codex plugin marketplace packaging.

---

## File Structure

- Create `examples/codex-memory-plugin/servers/experience-tools.mjs`: tool schemas, input validation, authenticated REST calls, response normalization.
- Create `examples/codex-memory-plugin/servers/experience-tools.test.mjs`: focused search/read adapter tests.
- Create `examples/codex-memory-plugin/skills/ov-experience-memory/SKILL.md`: bundled Codex skill.
- Modify `examples/memory-plugin-shared/lib/mcp-proxy-core.mjs`: optional local tool listing/call extension.
- Regenerate `examples/claude-code-memory-plugin/scripts/shared/mcp-proxy-core.mjs`, `examples/codex-memory-plugin/scripts/shared/mcp-proxy-core.mjs`, and `examples/opencode-plugin/lib/shared/mcp-proxy-core.mjs` with the repository sync script.
- Modify `examples/codex-memory-plugin/servers/mcp-proxy.mjs`: instantiate and pass the Experience adapter.
- Modify `examples/codex-memory-plugin/servers/mcp-proxy.test.mjs`: integration tests for tool listing, local dispatch, and upstream passthrough.
- Modify `examples/codex-memory-plugin/scripts/marketplace.test.mjs`: packaging checks for the bundled skill.
- Modify `examples/codex-memory-plugin/.codex-plugin/plugin.json`: advertise Experience Memory capability and bump the plugin patch version.

### Task 1: Lock the plugin package contract

**Files:**
- Modify: `examples/codex-memory-plugin/scripts/marketplace.test.mjs`
- Create: `examples/codex-memory-plugin/skills/ov-experience-memory/SKILL.md`

- [ ] **Step 1: Write the failing package test**

Add a test that requires `skills/ov-experience-memory/SKILL.md`, parses its frontmatter, and asserts the body names both official tools:

```javascript
test("plugin bundles the Experience Memory skill", () => {
  const skillPath = join(pluginDir, "skills", "ov-experience-memory", "SKILL.md");
  assert.ok(existsSync(skillPath), `missing bundled skill: ${skillPath}`);
  const content = readFileSync(skillPath, "utf-8");
  assert.match(content, /^---[\s\S]*name:\s*ov-experience-memory[\s\S]*---/);
  assert.match(content, /`search_experience`/);
  assert.match(content, /`read_experience`/);
});
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
node --test examples/codex-memory-plugin/scripts/marketplace.test.mjs
```

Expected: FAIL because the bundled Skill path does not exist.

- [ ] **Step 3: Add the bundled Skill**

Create the plugin Skill from the approved standalone contract in `examples/skills/ov-experience-memory/SKILL.md`. Keep the exact tool names and remove no runtime requirements.

- [ ] **Step 4: Run the package test and verify GREEN**

Run the Task 1 command. Expected: all marketplace tests pass.

### Task 2: Define and test Experience REST tools

**Files:**
- Create: `examples/codex-memory-plugin/servers/experience-tools.mjs`
- Create: `examples/codex-memory-plugin/servers/experience-tools.test.mjs`

- [ ] **Step 1: Write failing tests for tool definitions and search**

Tests must instantiate `createExperienceToolProvider({ fetchImpl })`, assert that `listTools()` returns the two exact names, and verify this search call:

```javascript
const result = await provider.callTool(
  { name: "search_experience", arguments: { query: "无订单号换货", limit: 5 } },
  { config },
);
```

The fake fetch must receive:

```json
{
  "query": "无订单号换货",
  "target_uri": "viking://user/memories/experiences/",
  "limit": 5
}
```

Assert that the MCP result text parses as `{results:[...]}` and excludes a returned preference URI.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
node --test examples/codex-memory-plugin/servers/experience-tools.test.mjs
```

Expected: FAIL because `experience-tools.mjs` does not exist.

- [ ] **Step 3: Implement the minimal provider**

Export:

```javascript
export function createExperienceToolProvider({ fetchImpl = globalThis.fetch } = {}) {
  return {
    listTools: () => EXPERIENCE_TOOL_DEFINITIONS,
    async callTool(params, { config }) {
      if (params.name === "search_experience") return searchExperience(params.arguments, config, fetchImpl);
      if (params.name === "read_experience") return readExperience(params.arguments, config, fetchImpl);
      return null;
    },
  };
}
```

Use the existing authentication headers: `Authorization`, `X-OpenViking-Account`, `X-OpenViking-User`, and optional `X-OpenViking-Actor-Peer`. Return JSON text inside MCP `content`.

- [ ] **Step 4: Add failing read and validation tests**

Cover a successful `/api/v1/content/read` request and rejection of `viking://user/test/memories/preferences/example.md`. Assert invalid input performs no fetch and returns `isError: true`.

- [ ] **Step 5: Implement read and validation, then verify GREEN**

Run the Task 2 test command. Expected: all Experience provider tests pass.

### Task 3: Add the optional local-tool extension to the proxy core

**Files:**
- Modify: `examples/memory-plugin-shared/lib/mcp-proxy-core.mjs`
- Modify: `examples/codex-memory-plugin/servers/mcp-proxy.test.mjs`
- Regenerate shared copies with `examples/memory-plugin-shared/sync.mjs`

- [ ] **Step 1: Write failing proxy integration tests**

Extend `makeProxy` to accept `localToolProvider`. Add tests asserting:

```javascript
await proxy.handleMessage({ jsonrpc: "2.0", id: 2, method: "tools/list" });
```

preserves an upstream `find` tool and appends both local definitions, and:

```javascript
await proxy.handleMessage({
  jsonrpc: "2.0",
  id: 3,
  method: "tools/call",
  params: { name: "search_experience", arguments: { query: "换货" } },
});
```

calls the local provider without forwarding that request upstream.

- [ ] **Step 2: Run proxy tests and verify RED**

```bash
node --test examples/codex-memory-plugin/servers/mcp-proxy.test.mjs
```

Expected: FAIL because the proxy ignores `localToolProvider`.

- [ ] **Step 3: Implement local dispatch in the shared source**

Add `localToolProvider` to `createOpenVikingMcpProxy`. For local `tools/call`, write a JSON-RPC result directly. For `tools/list`, forward upstream and append non-duplicate local definitions to each matching result. Leave all other methods unchanged.

- [ ] **Step 4: Synchronize generated modules**

```bash
node examples/memory-plugin-shared/sync.mjs
```

- [ ] **Step 5: Verify proxy and sync tests GREEN**

```bash
node --test \
  examples/codex-memory-plugin/servers/mcp-proxy.test.mjs \
  examples/memory-plugin-shared/sync.test.mjs
```

Expected: all tests pass.

### Task 4: Wire the Codex entrypoint and manifest

**Files:**
- Modify: `examples/codex-memory-plugin/servers/mcp-proxy.mjs`
- Modify: `examples/codex-memory-plugin/.codex-plugin/plugin.json`

- [ ] **Step 1: Add a failing entrypoint wiring assertion**

Update the marketplace test to require the entrypoint to import `createExperienceToolProvider` and pass `localToolProvider` to `createOpenVikingMcpProxy`.

- [ ] **Step 2: Run marketplace tests and verify RED**

Run the Task 1 command. Expected: FAIL on missing provider wiring.

- [ ] **Step 3: Wire the provider**

Instantiate the provider once in `servers/mcp-proxy.mjs` and pass it into the proxy constructor. Add `baseUrl` to `readProxyConfig` from the already resolved credentials. Bump the plugin version from `0.7.2` to `0.7.3` and advertise Experience search/read in `interface.capabilities`.

- [ ] **Step 4: Run marketplace and provider tests GREEN**

```bash
node --test \
  examples/codex-memory-plugin/scripts/marketplace.test.mjs \
  examples/codex-memory-plugin/servers/experience-tools.test.mjs \
  examples/codex-memory-plugin/servers/mcp-proxy.test.mjs
```

Expected: all tests pass.

### Task 5: Full verification and local plugin refresh

**Files:**
- Modify only if required by verified install behavior: `examples/codex-memory-plugin/README.md`

- [ ] **Step 1: Run the complete plugin test set**

```bash
node --test \
  examples/codex-memory-plugin/scripts/*.test.mjs \
  examples/codex-memory-plugin/servers/*.test.mjs \
  examples/memory-plugin-shared/sync.test.mjs
```

Expected: zero failures.

- [ ] **Step 2: Run syntax and repository checks**

```bash
node --check examples/codex-memory-plugin/servers/experience-tools.mjs
node --check examples/codex-memory-plugin/servers/mcp-proxy.mjs
git diff --check
```

- [ ] **Step 3: Refresh the locally installed plugin**

Use the existing local marketplace flow to reinstall or refresh `openviking-memory@openviking-plugins-local`, set its plugin entry to `enabled = true`, and restart Codex so the MCP process reloads version `0.7.3`.

- [ ] **Step 4: Verify against Docker OpenViking**

Confirm `tools/list` includes both official tools. Call `search_experience` for “无订单号换货”, call `read_experience` on the returned URI, then commit the captured session through the plugin lifecycle. Verify Kafka receives `memory.recalled` and `memory.injected` for the same URI.

- [ ] **Step 5: Commit implementation**

Stage only plugin, shared proxy, generated copies, tests, Skill, and directly related docs. Commit with:

```bash
git commit -m "feat: add Codex experience memory tools"
```
