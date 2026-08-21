// Pure helpers that render OpenViking MCP configuration for the three Copilot
// surfaces documented in INSTALL.md:
//
//   * VSCode Copilot Chat / agent mode -> .vscode/mcp.json (uses "servers")
//   * GitHub Copilot CLI (`gh copilot`) -> ~/.copilot/mcp-config.json (uses "mcpServers")
//   * GitHub.com repo-level cloud agent / code review -> Settings -> Copilot -> MCP servers
//
// These functions are intentionally side-effect free so they can be unit-tested
// and reused by setup-helper/install.sh.

// Tools enabled by default for surfaces where the user is in the loop
// (VSCode Copilot Chat, `gh copilot` CLI). Includes `remember` so the model
// can persist durable facts when the user asks.
const DEFAULT_TOOLS = [
  "recall",
  "search",
  "find",
  "remember",
  "read",
  "list",
  "grep",
  "glob",
];

// Read-only default for autonomous surfaces. The GitHub.com Copilot cloud
// agent and Copilot code review invoke enabled tools WITHOUT asking for
// approval, so the safe default excludes write tools (`remember`,
// `add_resource`, `forget`, `cancel_watch`). Users who want the cloud agent
// to persist memories can pass `options.tools` explicitly.
const CLOUD_AGENT_DEFAULT_TOOLS = [
  "recall",
  "search",
  "find",
  "read",
  "list",
  "grep",
  "glob",
];

/**
 * Validate and normalise a connection spec.
 * Throws on invalid input so callers (installer / tests) fail loud rather
 * than producing a config that silently points at the wrong server.
 */
export function normalizeSpec(spec = {}) {
  const url = String(spec.url || "").trim();
  if (!url) throw new Error("OPENVIKING_URL is required");
  if (!/^https?:\/\//i.test(url)) {
    throw new Error(`OPENVIKING_URL must start with http:// or https:// (got: ${url})`);
  }
  const apiKey = spec.apiKey ? String(spec.apiKey).trim() : "";
  return { url, apiKey, serverName: spec.serverName || "openviking" };
}

/**
 * VSCode Copilot Chat / agent mode config (.vscode/mcp.json or user profile).
 *
 * Note: VSCode's top-level key is `servers`, NOT `mcpServers`. A remote HTTP
 * server uses `{ type: "http", url, headers }`. There is no `tools` allowlist
 * field at this layer (VSCode handles tool toggling in the UI).
 *
 * Source: https://code.visualstudio.com/docs/agent-customization/mcp-servers (2026-07-28)
 */
export function buildVscodeConfig(spec = {}) {
  const { url, apiKey, serverName } = normalizeSpec(spec);
  const server = { type: "http", url };
  if (apiKey) server.headers = { Authorization: `Bearer ${apiKey}` };
  return { servers: { [serverName]: server } };
}

/**
 * GitHub Copilot CLI (`gh copilot`) config (~/.copilot/mcp-config.json).
 *
 * Top-level key is `mcpServers`. HTTP entries accept `type: "http"`, `url`,
 * `headers`, and `tools` (string[]; `["*"]` enables all).
 *
 * Source: https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-mcp-servers (2026-07-28)
 */
export function buildCopilotCliConfig(spec = {}, options = {}) {
  const { url, apiKey, serverName } = normalizeSpec(spec);
  const server = {
    type: "http",
    url,
    tools: options.tools || DEFAULT_TOOLS.slice(),
  };
  if (apiKey) server.headers = { Authorization: `Bearer ${apiKey}` };
  return { mcpServers: { [serverName]: server } };
}

/**
 * GitHub.com repository-level config for Copilot cloud agent + code review.
 *
 * Lives under repo Settings -> Copilot -> MCP servers (NOT a committed file by
 * default; the JSON is pasted into the GitHub UI). Required keys are `type`
 * and `tools`. Secrets MUST be referenced as `${COPILOT_MCP_*}` and configured
 * as Agents secrets — never hard-coded.
 *
 * Caveats (2026-07-28, public preview):
 *   - Repo-level Copilot supports tools only; MCP resources/prompts are ignored.
 *   - OAuth-protected remote MCP servers are NOT supported; use API-key auth.
 *
 * Source: https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/configure-mcp-servers
 */
export function buildGithubRepoConfig(spec = {}, options = {}) {
  const { url, serverName } = normalizeSpec(spec);
  const tools = options.tools || CLOUD_AGENT_DEFAULT_TOOLS.slice();
  // Force secret-variable form for the API key when one is expected: pasting a
  // real key into the GitHub UI would leak it.
  const apiKeyRef = options.apiKeySecret || "${COPILOT_MCP_OPENVIKING_API_KEY}";
  return {
    mcpServers: {
      [serverName]: {
        type: "http",
        url,
        headers: { Authorization: `Bearer ${apiKeyRef}` },
        tools,
      },
    },
  };
}

/**
 * Optional stdio-proxy config for users who want OpenViking credentials
 * auto-resolved from ~/.openviking/ovcli.conf (or who run an unauthenticated
 * local server). Valid in both .vscode/mcp.json (under "servers") and
 * ~/.copilot/mcp-config.json (under "mcpServers" with type: "local").
 */
export function buildStdioProxyConfig(spec = {}, options = {}) {
  // NOTE: stdio-proxy configs do NOT need a URL/API key here. The proxy
  // resolves credentials itself at runtime from ~/.openviking/ovcli.conf
  // (same as the codex/zcode/cursor plugins), so we only need a serverName
  // and the plugin root. `spec` is accepted for signature symmetry but its
  // url/apiKey are intentionally ignored.
  const serverName = (spec && spec.serverName) || "openviking";
  const pluginRoot = options.pluginRoot || "__OPENVIKING_COPILOT_ROOT__";
  const command = options.nodeBin || "node";
  const entry = `${pluginRoot}/servers/mcp-proxy.mjs`;
  // VSCode form (no "type", uses command/args). Copilot CLI accepts this same
  // shape inside `mcpServers` with `type: "local"` — caller wraps accordingly.
  return {
    serverName,
    command,
    args: [entry],
  };
}

export const _internals = { DEFAULT_TOOLS, CLOUD_AGENT_DEFAULT_TOOLS };
