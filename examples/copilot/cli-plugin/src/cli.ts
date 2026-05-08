/**
 * CLI scaffolding for the OpenViking MCP server bin
 * (`openviking-copilot-mcp`).
 *
 * Issue #20 sets up:
 *   - `--help` / `-h` — usage text
 *   - `--version` / `-v` — package version
 *   - `--check` — load PluginConfig and print a redacted summary so
 *     users can verify their `mcp-config.json` env passthrough is wired up
 *   - default invocation (no flags) — start the stdio MCP server
 *
 * Issue #27 adds:
 *   - `--commit-flush --session=<id>` — load PluginConfig, build an
 *     OVClient, force-commit `<id>`. Used by the `copilot()` shell-
 *     wrapper fallback at end-of-session so any pending turns the
 *     model captured (via `openviking_capture`) but didn't trigger
 *     a threshold commit for actually land as archives. Exits 0 on
 *     success or bypass-short-circuit; non-zero on transport error.
 *
 * `runMain(argv, opts)` is exported as a vitest-friendly entry point;
 * the bin shim (`mcp-server.ts`) just calls it with `process.argv`.
 *
 * The function takes a duck-typed write/loadConfig shim so tests
 * inject scripted output streams + a fake config without needing a
 * real ovcli.conf on disk.
 */

import {
  createDebugLogger,
  isPluginEnabled as defaultIsEnabled,
  loadConfig as defaultLoadConfig,
  OVClient,
  runDebugCapture,
  runDebugRecall,
  type DebugCaptureResult,
  type DebugRecallResult,
  type LoadConfigOptions,
  type OVResult,
  type PluginConfig,
  type RecallDebuggerClient,
} from "@openviking/copilot-shared";
import { runStdioMcpServer as defaultRunStdioMcpServer } from "./server.js";

/** Pinned to `package.json#version` at build time by esbuild's define. */
declare const __OV_CLI_VERSION__: string;

const VERSION = (typeof __OV_CLI_VERSION__ !== "undefined" ? __OV_CLI_VERSION__ : "0.0.0");

const HELP = `\
openviking-copilot-mcp [options]

OpenViking memory MCP server for the GitHub Copilot CLI. Mounted via
the CLI's mcp-config.json configuration. The server reads connection
settings from \`~/.openviking/ovcli.conf\` and \`OPENVIKING_*\`
environment variables (priority: env > ovcli.conf > ov.conf >
defaults).

Options:
  -h, --help                   Show this help message and exit
  -v, --version                Show package version and exit
  --check                      Load PluginConfig and print a redacted
                               summary (verifies your mcp-config.json env
                               passthrough is wired up)
  --commit-flush --session=ID  Force-commit the given OpenViking
                               session (used by the copilot() shell-
                               wrapper fallback at end-of-session)
  --debug-recall=QUERY         Run the recall pipeline against QUERY
                               and print a verbose diagnostic report
                               (config snapshot, health, ranking,
                               final block + telemetry)
  --debug-capture=PATH         Load a transcript JSON file (an array
                               of {role,text} objects) and print the
                               sanitise + canonicalise + token-
                               threshold projection

Configuration files (read-only):
  ~/.openviking/ovcli.conf   url, api_key, account, user, agent_id
  ~/.openviking/ov.conf      copilot block (legacy: claude_code)

Environment variables (subset):
  OPENVIKING_URL, OPENVIKING_API_KEY, OPENVIKING_ACCOUNT,
  OPENVIKING_USER, OPENVIKING_AGENT_ID, OPENVIKING_MEMORY_ENABLED,
  OPENVIKING_DEBUG, OPENVIKING_CLI_SESSION_ID

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
  /**
   * Inject for tests — replaces the default OVClient.commit
   * invocation that `--commit-flush` triggers. Takes the resolved
   * cfg and the target session id; returns the OVResult so the
   * caller can map success/error to exit codes.
   */
  commitFlush?: (cfg: PluginConfig, sessionId: string) => Promise<OVResult<unknown>>;
  /**
   * Inject for tests — overrides the recall/capture diagnostic
   * runners so they don't open real network/file handles.
   */
  debugRecallRunner?: (cfg: PluginConfig, query: string) => Promise<DebugRecallResult>;
  debugCaptureRunner?: (cfg: PluginConfig, path: string) => Promise<DebugCaptureResult>;
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
  const commitFlush = opts.commitFlush ?? defaultCommitFlush;
  const debugRecallRunner = opts.debugRecallRunner ?? defaultDebugRecallRunner;
  const debugCaptureRunner = opts.debugCaptureRunner ?? defaultDebugCaptureRunner;

  // Single-pass argv parser — handles --flag and --key=value forms.
  const flags = new Set<string>();
  let sessionArg: string | undefined;
  let debugRecallArg: string | undefined;
  let debugCaptureArg: string | undefined;
  for (const a of argv) {
    if (a === "-h") flags.add("--help");
    else if (a === "-v") flags.add("--version");
    else if (a.startsWith("--session=")) {
      sessionArg = a.slice("--session=".length);
      flags.add("--session");
    } else if (a.startsWith("--debug-recall=")) {
      debugRecallArg = a.slice("--debug-recall=".length);
      flags.add("--debug-recall");
    } else if (a.startsWith("--debug-capture=")) {
      debugCaptureArg = a.slice("--debug-capture=".length);
      flags.add("--debug-capture");
    } else if (a.startsWith("--")) {
      flags.add(a);
    } else {
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
  if (flags.has("--commit-flush")) {
    return runCommitFlushCommand({
      loadConfig,
      commitFlush,
      sessionId: sessionArg,
      out,
      err,
    });
  }
  if (flags.has("--debug-recall")) {
    return runDebugRecallCommand({
      loadConfig,
      runner: debugRecallRunner,
      query: debugRecallArg,
      out,
      err,
    });
  }
  if (flags.has("--debug-capture")) {
    return runDebugCaptureCommand({
      loadConfig,
      runner: debugCaptureRunner,
      path: debugCaptureArg,
      out,
      err,
    });
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

async function runCommitFlushCommand(args: {
  loadConfig: (opts: LoadConfigOptions) => PluginConfig;
  commitFlush: (cfg: PluginConfig, sessionId: string) => Promise<OVResult<unknown>>;
  sessionId: string | undefined;
  out: (c: string) => void;
  err: (c: string) => void;
}): Promise<number> {
  if (!args.sessionId || !args.sessionId.trim()) {
    args.err("--commit-flush requires --session=<id>\n");
    return 2;
  }
  const cfg = args.loadConfig({ agentIdDefault: "copilot-cli" });
  const res = await args.commitFlush(cfg, args.sessionId.trim());
  if (!res.ok) {
    args.err(
      `commit-flush failed: ${res.error.status ? `HTTP ${res.error.status}: ` : ""}${res.error.message}\n`,
    );
    return 1;
  }
  return 0;
}

/**
 * Default `--commit-flush` implementation: build an OVClient from the
 * resolved cfg and force-commit the session. Bypass is honoured by
 * OVClient itself (returns ok with skipped:true), so this never
 * needs an explicit isBypassed branch.
 */
async function defaultCommitFlush(cfg: PluginConfig, sessionId: string): Promise<OVResult<unknown>> {
  const client = new OVClient(cfg);
  return client.commit(sessionId, { force: true });
}

async function runDebugRecallCommand(args: {
  loadConfig: (opts: LoadConfigOptions) => PluginConfig;
  runner: (cfg: PluginConfig, query: string) => Promise<DebugRecallResult>;
  query: string | undefined;
  out: (c: string) => void;
  err: (c: string) => void;
}): Promise<number> {
  const query = args.query?.trim();
  if (!query) {
    args.err("--debug-recall requires a query: --debug-recall=<prompt>\n");
    return 2;
  }
  const cfg = args.loadConfig({ agentIdDefault: "copilot-cli" });
  const res = await args.runner(cfg, query);
  args.out(res.output);
  return res.exitCode;
}

async function runDebugCaptureCommand(args: {
  loadConfig: (opts: LoadConfigOptions) => PluginConfig;
  runner: (cfg: PluginConfig, path: string) => Promise<DebugCaptureResult>;
  path: string | undefined;
  out: (c: string) => void;
  err: (c: string) => void;
}): Promise<number> {
  const path = args.path?.trim();
  if (!path) {
    args.err("--debug-capture requires a transcript file path: --debug-capture=<path>\n");
    return 2;
  }
  const cfg = args.loadConfig({ agentIdDefault: "copilot-cli" });
  const res = await args.runner(cfg, path);
  args.out(res.output);
  return res.exitCode;
}

async function defaultDebugRecallRunner(cfg: PluginConfig, query: string): Promise<DebugRecallResult> {
  const logger = createDebugLogger(cfg, { scope: "debug-recall" });
  const client: RecallDebuggerClient = new OVClient(cfg, { logger });
  return runDebugRecall({ query }, { cfg, client });
}

async function defaultDebugCaptureRunner(cfg: PluginConfig, path: string): Promise<DebugCaptureResult> {
  return runDebugCapture({ path }, { cfg });
}
