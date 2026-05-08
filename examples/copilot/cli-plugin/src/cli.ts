/**
 * CLI scaffolding for the OpenViking MCP server bin
 * (`openviking-copilot-mcp`).
 *
 * Issue #20 sets up:
 *   - `--help` / `-h` — usage text
 *   - `--version` / `-v` — package version
 *   - `--check` — load PluginConfig and print a redacted summary so
 *     users can verify their `mcp.json` env passthrough is wired up
 *   - default invocation (no flags) — start the stdio MCP server
 *
 * `runMain(argv, opts)` is exported as a vitest-friendly entry point;
 * the bin shim (`mcp-server.ts`) just calls it with `process.argv`.
 *
 * The function takes a duck-typed write/loadConfig shim so tests
 * inject scripted output streams + a fake config without needing a
 * real ovcli.conf on disk.
 */

import {
  isPluginEnabled as defaultIsEnabled,
  loadConfig as defaultLoadConfig,
  type LoadConfigOptions,
  type PluginConfig,
} from "@openviking/copilot-shared";
import { runStdioMcpServer as defaultRunStdioMcpServer } from "./server.js";

/** Pinned to `package.json#version` at build time by esbuild's define. */
declare const __OV_CLI_VERSION__: string;

const VERSION = (typeof __OV_CLI_VERSION__ !== "undefined" ? __OV_CLI_VERSION__ : "0.0.0");

const HELP = `\
openviking-copilot-mcp [options]

OpenViking memory MCP server for the GitHub Copilot CLI. Mounted via
the CLI's mcp.json configuration. The server reads connection
settings from \`~/.openviking/ovcli.conf\` and \`OPENVIKING_*\`
environment variables (priority: env > ovcli.conf > ov.conf >
defaults).

Options:
  -h, --help       Show this help message and exit
  -v, --version    Show package version and exit
  --check          Load PluginConfig and print a redacted summary
                   (verifies your mcp.json env passthrough is wired)

Configuration files (read-only):
  ~/.openviking/ovcli.conf   url, api_key, account, user, agent_id
  ~/.openviking/ov.conf      copilot block (legacy: claude_code)

Environment variables (subset):
  OPENVIKING_URL, OPENVIKING_API_KEY, OPENVIKING_ACCOUNT,
  OPENVIKING_USER, OPENVIKING_AGENT_ID, OPENVIKING_MEMORY_ENABLED,
  OPENVIKING_DEBUG

See PLAN.md §8 for the full priority chain and field list.
`;

export interface RunMainStreams {
  stdout?: (chunk: string) => void;
  stderr?: (chunk: string) => void;
}

export interface RunMainDeps {
  loadConfig?: (opts: LoadConfigOptions) => PluginConfig;
  isPluginEnabled?: () => boolean;
  runStdioMcpServer?: (opts: { version: string; loadConfig: (opts: LoadConfigOptions) => PluginConfig }) => Promise<void>;
}

export interface RunMainOptions extends RunMainStreams, RunMainDeps {}

/**
 * Run the bin with the given argv (without the leading `node script`
 * elements). Returns a process exit code; never throws — argv parse
 * errors print to stderr and exit non-zero.
 */
export async function runMain(
  argv: string[],
  opts: RunMainOptions = {},
): Promise<number> {
  const out = opts.stdout ?? ((c) => process.stdout.write(c));
  const err = opts.stderr ?? ((c) => process.stderr.write(c));
  const loadConfig = opts.loadConfig ?? defaultLoadConfig;
  const isEnabled = opts.isPluginEnabled ?? defaultIsEnabled;
  const runStdioMcpServer = opts.runStdioMcpServer ?? defaultRunStdioMcpServer;

  // Single-pass argv parser — only --flag forms, no values.
  const flags = new Set<string>();
  for (const a of argv) {
    if (a === "-h") flags.add("--help");
    else if (a === "-v") flags.add("--version");
    else if (a.startsWith("--")) flags.add(a);
    else {
      err(`Unknown positional argument: ${a}\n`);
      err("Run with --help for usage.\n");
      return 2;
    }
  }

  if (flags.has("--help")) {
    out(HELP);
    return 0;
  }
  if (flags.has("--version")) {
    out(`${VERSION}\n`);
    return 0;
  }
  if (flags.has("--check")) {
    return runConfigCheck({ loadConfig, isEnabled, out, err });
  }

  await runStdioMcpServer({ version: VERSION, loadConfig });
  return 0;
}

function runConfigCheck(args: {
  loadConfig: (opts: LoadConfigOptions) => PluginConfig;
  isEnabled: () => boolean;
  out: (c: string) => void;
  err: (c: string) => void;
}): number {
  const cfg = args.loadConfig({ agentIdDefault: "copilot-cli" });
  const enabled = args.isEnabled();

  const lines = [
    `OpenViking Copilot CLI plugin — config check`,
    ``,
    `enabled        : ${enabled}`,
    `configPath     : ${cfg.configPath ?? "(none)"}`,
    `baseUrl        : ${cfg.baseUrl}`,
    `apiKey         : ${cfg.apiKey ? `<set, ${cfg.apiKey.length} chars>` : "<unset>"}`,
    `accountId      : ${cfg.accountId || "<unset>"}`,
    `userId         : ${cfg.userId || "<unset>"}`,
    `agentId        : ${cfg.agentId}`,
    `autoRecall     : ${cfg.autoRecall}`,
    `autoCapture    : ${cfg.autoCapture}`,
    `debug          : ${cfg.debug}`,
    `bypassSession  : ${cfg.bypassSession}`,
    `bypassPatterns : ${cfg.bypassSessionPatterns.length === 0 ? "[]" : JSON.stringify(cfg.bypassSessionPatterns)}`,
    ``,
  ];
  args.out(lines.join("\n"));
  return enabled ? 0 : 3;
}
