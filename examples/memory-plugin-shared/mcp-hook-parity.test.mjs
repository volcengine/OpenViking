/**
 * A harness's hooks and its MCP proxy must reach the server as the same caller.
 *
 * They run in different processes, and the proxy's process sees only what its
 * host hands it: Codex forwards the names in `.mcp.json`'s `env_vars`, DSH
 * drops credential-shaped names and adds the bundle's forwarded connection,
 * the others inherit everything. Each
 * row below states that rule, and every scenario runs the hook loader on the
 * full environment and the proxy on what that rule lets through, then compares
 * what goes on the wire. Comparing the resolved connection alone would miss
 * exactly the drift this exists for: a variable the proxy never receives.
 */

import assert from "node:assert/strict";
import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { rm } from "node:fs/promises";
import { join } from "node:path";
import { after, before, test } from "node:test";

import { loadAgentHookConfig } from "./lib/agent-hook-runtime.mjs";
import { ROOT } from "./sync.mjs";
import { scrubOpenVikingEnv, writeCredentialFiles } from "./testing/support.mjs";
import { HOSTS } from "../agent-hook-plugin/hosts/index.mjs";
import { readProxyConfig as agentHookProxy } from "../agent-hook-plugin/servers/mcp-proxy.mjs";
import { loadConfig as loadClaudeCode } from "../claude-code-memory-plugin/scripts/config.mjs";
import { readProxyConfig as claudeCodeProxy } from "../claude-code-memory-plugin/servers/mcp-proxy.mjs";
import { loadConfig as loadCodex } from "../codex-memory-plugin/scripts/config.mjs";
import { readProxyConfig as codexProxy } from "../codex-memory-plugin/servers/mcp-proxy.mjs";
import { resolveConfig as loadDsh } from "../dsh-memory-plugin/config.mjs";
import { buildMcpConfig as dshMcpConfig } from "../dsh-memory-plugin/mcp-env.mjs";
import { readProxyConfig as dshProxy } from "../dsh-memory-plugin/servers/mcp-proxy.mjs";
import { loadConfig as loadOpencode } from "../opencode-plugin/lib/config.mjs";
import { readProxyConfig as opencodeProxy } from "../opencode-plugin/servers/mcp-proxy.mjs";

const WIRE = ["mcpUrl", "apiKey", "account", "user", "sendIdentityHeaders"];
const wire = (cfg) => Object.fromEntries(WIRE.map((field) => [field, cfg[field]]));
const pick = (env, names) => Object.fromEntries(names.filter((name) => name in env).map((name) => [name, env[name]]));

const CODEX_ENV_VARS = JSON.parse(readFileSync(join(ROOT, "examples", "codex-memory-plugin", ".mcp.json"), "utf-8"))
  .mcpServers["openviking-memory"].env_vars;

// What a DSH child inherits: the parent's environment minus the names
// `@deepseek-ai/dsh-subprocess` treats as credentials.
const DSH_SCRUBBED = /KEY|PASSWORD|SECRET|TOKEN/i;
const dshInherited = (env) => Object.fromEntries(Object.entries(env).filter(([name]) => !DSH_SCRUBBED.test(name)));

const HARNESSES = [
  {
    name: "codex",
    dir: "codex-memory-plugin",
    section: "codex",
    hook: ({ env, cwd }) => loadCodex(cwd, { env }),
    proxy: ({ env }) => codexProxy(pick(env, CODEX_ENV_VARS)),
  },
  {
    name: "claude-code",
    dir: "claude-code-memory-plugin",
    section: "claude_code",
    rootKeyFallback: true,
    hook: ({ env, cwd }) => loadClaudeCode(cwd, { env }),
    proxy: ({ env }) => claudeCodeProxy(env),
  },
  {
    name: "opencode",
    dir: "opencode-plugin",
    section: "opencode",
    hook: ({ env, cwd }) => loadOpencode("", cwd, { env }),
    proxy: ({ env }) => opencodeProxy(env),
  },
  // The installer names the client in the MCP server's environment.
  ...Object.keys(HOSTS).map((client) => ({
    name: client,
    dir: "agent-hook-plugin",
    section: client.replace(/-/g, "_"),
    hook: ({ env, cwd }) => loadAgentHookConfig(client, cwd, { env }),
    proxy: ({ env }) => agentHookProxy({ ...env, OPENVIKING_HOOK_SOURCE: client }),
  })),
  // A hand-written MCP entry that names no client.
  {
    name: "agent-hook",
    dir: "agent-hook-plugin",
    section: "agent_hook",
    hook: ({ env, cwd }) => loadAgentHookConfig("agent-hook", cwd, { env }),
    proxy: ({ env }) => agentHookProxy(env),
  },
  {
    name: "dsh",
    dir: "dsh-memory-plugin",
    section: "dsh",
    hook: ({ env, cwd, host }) => loadDsh({ workspacePeer: false, ...host }, env, cwd),
    proxy: ({ env, hook, otherDir }) => dshProxy({ ...dshInherited(env), ...dshMcpConfig(hook).env }, otherDir),
  },
];

const URL = "https://ov.example.com";

/**
 * `ovcli` and `ov` are the two files, or a function of the harness for a
 * section named after it; `env` is added to the hook's environment; `host` is
 * what an embedding host passes (dsh reads it, the rest have none); `expect`
 * pins the answer as well, so both sides cannot agree on a wrong one.
 */
const SCENARIOS = [
  {
    name: "ovcli.conf's own key and identity",
    ovcli: { url: URL, api_key: "sk-cli", account: "acct-cli", user: "usr-cli" },
  },
  {
    name: "the key and identity in plugin.<harness>",
    ovcli: (h) => ({ url: URL, plugin: { [h.section]: { apiKey: "sk-plugin-h", accountId: "acct-plugin-h", userId: "usr-plugin-h" } } }),
    expect: () => ({ apiKey: "sk-plugin-h", account: "acct-plugin-h", user: "usr-plugin-h", sendIdentityHeaders: true }),
  },
  {
    name: "the shared plugin section",
    ovcli: { url: URL, plugin: { apiKey: "sk-plugin", accountId: "acct-plugin", authMode: "api_key" } },
    expect: () => ({ apiKey: "sk-plugin", sendIdentityHeaders: false }),
  },
  {
    name: "a url-only ovcli.conf over ov.conf's harness key",
    ovcli: { url: URL },
    ov: (h) => ({ server: { root_api_key: "sk-root" }, [h.section]: { apiKey: "sk-ov-h" } }),
    expect: () => ({ apiKey: "sk-ov-h" }),
  },
  {
    name: "credential variables taking the chain off ovcli.conf",
    ovcli: { url: URL, api_key: "sk-cli", account: "acct-cli" },
    env: { OPENVIKING_URL: "https://env.example.com", OPENVIKING_API_KEY: "sk-env" },
    expect: () => ({ mcpUrl: "https://env.example.com/mcp", apiKey: "sk-env" }),
  },
  {
    name: "OPENVIKING_AUTH_MODE keeping the identity off the wire",
    ovcli: { url: URL, api_key: "sk-cli", account: "acct-cli", user: "usr-cli" },
    env: { OPENVIKING_AUTH_MODE: "api_key" },
    expect: () => ({ sendIdentityHeaders: false }),
  },
  {
    name: "a forced ovcli.conf source over stale credentials in the environment",
    ovcli: { url: URL, api_key: "sk-cli" },
    env: { OPENVIKING_CREDENTIAL_SOURCE: "cli", OPENVIKING_API_KEY: "sk-stale", OPENVIKING_ACCOUNT: "acct-stale" },
    expect: () => ({ apiKey: "sk-cli", account: "", sendIdentityHeaders: false }),
  },
  {
    name: "a url-only ovcli.conf over the root key",
    ovcli: { url: URL },
    ov: { server: { root_api_key: "sk-root" } },
    expect: (h) => ({ apiKey: h.rootKeyFallback ? "sk-root" : "" }),
  },
  {
    name: "an install with only ov.conf",
    ov: (h) => ({ server: { url: URL, root_api_key: "sk-root", auth_mode: "trusted" }, [h.section]: { accountId: "acct-ov-h" } }),
  },
  {
    name: "an explicit MCP URL",
    ovcli: { url: URL, api_key: "sk-cli" },
    env: { OPENVIKING_MCP_URL: "https://mcp.example.com/custom" },
    expect: () => ({ mcpUrl: "https://mcp.example.com/custom" }),
  },
  {
    name: "a connection the host named",
    ovcli: { url: URL, api_key: "sk-cli", account: "acct-cli" },
    host: { endpoint: "https://host.example.com", apiKey: "sk-host", user: "usr-host", authMode: "api_key" },
  },
  {
    name: "a workspace file that tries to move the connection",
    ovcli: { url: URL, api_key: "sk-cli", account: "acct-cli" },
    workspace: {
      version: 1,
      url: "https://workspace.example.com",
      api_key: "sk-workspace",
      auth_mode: "api_key",
      peer: { id: "team-a" },
      recall: { max_items: 3, peer_scope: "actor" },
    },
    expect: () => ({ mcpUrl: `${URL}/mcp`, apiKey: "sk-cli", account: "acct-cli", sendIdentityHeaders: true }),
  },
];

let restoreEnv;
before(() => { restoreEnv = scrubOpenVikingEnv(); });
after(() => restoreEnv());

const resolveFile = (file, harness) => (typeof file === "function" ? file(harness) : file);

async function runScenario(scenario, harness) {
  const files = await writeCredentialFiles("ov-parity-", {
    ovcli: resolveFile(scenario.ovcli, harness),
    ov: resolveFile(scenario.ov, harness),
  });
  try {
    // The hook runs in a workspace; the proxy runs wherever its host started it.
    const cwd = join(files.dir, "workspace");
    const otherDir = join(files.dir, "other");
    mkdirSync(join(cwd, ".git"), { recursive: true });
    mkdirSync(join(cwd, ".openviking"), { recursive: true });
    mkdirSync(otherDir, { recursive: true });
    if (scenario.workspace) {
      writeFileSync(join(cwd, ".openviking", "config.json"), JSON.stringify(scenario.workspace));
    }
    const env = { ...files.env, ...scenario.env };
    const hook = harness.hook({ env, cwd, host: scenario.host || {} });
    const proxy = harness.proxy({ env, hook, otherDir });
    return { hook: wire(hook), proxy: wire(proxy) };
  } finally {
    await rm(files.dir, { recursive: true, force: true });
  }
}

for (const scenario of SCENARIOS) {
  test(`hooks and MCP proxy agree: ${scenario.name}`, async () => {
    for (const harness of HARNESSES) {
      const { hook, proxy } = await runScenario(scenario, harness);
      assert.deepEqual(proxy, hook, `${harness.name}: the MCP proxy would reach the server as someone else`);
      if (scenario.expect) {
        const expected = scenario.expect(harness);
        assert.deepEqual(
          Object.fromEntries(Object.keys(expected).map((field) => [field, hook[field]])),
          expected,
          `${harness.name}: both sides agree, on the wrong answer`,
        );
      }
    }
  });
}

test("every proxy that ships beside hooks has a row, and so does every hook client", () => {
  const shipped = readdirSync(join(ROOT, "examples"), { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && existsSync(join(ROOT, "examples", entry.name, "servers", "mcp-proxy.mjs")))
    .map((entry) => entry.name)
    .sort();
  assert.deepEqual([...new Set(HARNESSES.map((harness) => harness.dir))].sort(), shipped);

  const names = new Set(HARNESSES.map((harness) => harness.name));
  for (const client of Object.keys(HOSTS)) assert.ok(names.has(client), `${client} needs a row`);
});
