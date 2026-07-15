import type { CommandDefinition, CommandResult, PluginCommandContext } from "./command-registration.js";
import type { RecallTraceEntry, RecallTraceSource } from "../recall-trace.js";

export type OpenVikingCommandSession = {
  sessionId?: string;
  sessionKey?: string;
  ovSessionId?: string;
  agentId: string;
  actorPeerId?: string;
};

export type OpenVikingCommandToolResult = {
  content: Array<{ type: "text"; text: string }>;
  details?: Record<string, unknown>;
};

export type AddResourceCommandInput = {
  source?: string;
  to?: string;
  parent?: string;
  reason?: string;
  instruction?: string;
  wait?: boolean;
  timeout?: number;
};

export type AddSkillCommandInput = {
  source?: string;
  data?: unknown;
  wait?: boolean;
  timeout?: number;
};

export type OVSearchCommandInput = {
  query: string;
  uri?: string;
  limit?: number;
};

export type RecallTraceCommandInput = {
  turn?: "latest" | "all";
  traceId?: string;
  sessionId?: string;
  sessionKey?: string;
  ovSessionId?: string;
  source?: RecallTraceSource;
  resourceTypes?: string;
  since?: number;
  until?: number;
  includeContent?: boolean;
  limit?: number;
};

export type RecallTraceCommandResult = {
  entries: RecallTraceEntry[];
  lookupLayer: string;
  warnings: string[];
};

export type OpenVikingCommandDefinitionsDeps = {
  resolvePluginSessionRouting: (ctx?: PluginCommandContext) => OpenVikingCommandSession;
  isBypassedSession: (ctx?: PluginCommandContext) => boolean;
  makeBypassedToolResult: (toolName: string) => OpenVikingCommandToolResult;
  parseAddResourceCommandArgs: (args: string) => AddResourceCommandInput;
  parseAddSkillCommandArgs: (args: string) => AddSkillCommandInput;
  parseOVSearchCommandArgs: (args: string) => OVSearchCommandInput;
  addResourceOpenViking: (input: AddResourceCommandInput, agentId?: string) => Promise<OpenVikingCommandToolResult>;
  addSkillOpenViking: (input: AddSkillCommandInput, agentId?: string) => Promise<OpenVikingCommandToolResult>;
  searchOpenViking: (
    input: OVSearchCommandInput,
    agentId?: string,
    traceCtx?: OpenVikingCommandSession,
  ) => Promise<OpenVikingCommandToolResult>;
  handleQueryConfigCommand: (ctx: PluginCommandContext) => Promise<CommandResult>;
  queryRecallTraces: (
    input: RecallTraceCommandInput,
    session: OpenVikingCommandSession,
  ) => Promise<RecallTraceCommandResult>;
  formatRecallTraceText: (result: RecallTraceCommandResult) => string;
};

function tokenizeCommandArgs(args: string): string[] {
  const tokens: string[] = [];
  let current = "";
  let quote: "'" | '"' | null = null;
  let escaping = false;

  for (let i = 0; i < args.length; i += 1) {
    const ch = args[i]!;
    const next = args[i + 1];
    if (escaping) {
      current += ch;
      escaping = false;
      continue;
    }
    if (ch === "\\") {
      const shouldEscape =
        quote === '"'
          ? next === '"' || next === "\\"
          : !quote && Boolean(next && (/\s/.test(next) || next === '"' || next === "'"));
      if (shouldEscape) {
        escaping = true;
        continue;
      }
      current += ch;
      continue;
    }
    if ((ch === '"' || ch === "'") && (!quote || quote === ch)) {
      quote = quote ? null : ch;
      continue;
    }
    if (!quote && /\s/.test(ch)) {
      if (current) {
        tokens.push(current);
        current = "";
      }
      continue;
    }
    current += ch;
  }

  if (escaping) {
    current += "\\";
  }
  if (quote) {
    throw new Error("Unterminated quoted argument");
  }
  if (current) {
    tokens.push(current);
  }
  return tokens;
}

function parseFlagArgs(args: string): { flags: Map<string, string | boolean> } {
  const tokens = tokenizeCommandArgs(args);
  const flags = new Map<string, string | boolean>();

  for (let i = 0; i < tokens.length; i += 1) {
    const token = tokens[i]!;
    if (!token.startsWith("--")) {
      continue;
    }
    const raw = token.slice(2);
    if (!raw) {
      continue;
    }
    const eqIndex = raw.indexOf("=");
    if (eqIndex >= 0) {
      flags.set(raw.slice(0, eqIndex), raw.slice(eqIndex + 1));
      continue;
    }
    const next = tokens[i + 1];
    if (next && !next.startsWith("--")) {
      flags.set(raw, next);
      i += 1;
    } else {
      flags.set(raw, true);
    }
  }

  return { flags };
}

function getStringFlag(flags: Map<string, string | boolean>, name: string): string | undefined {
  const value = flags.get(name);
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function getNumberFlag(flags: Map<string, string | boolean>, name: string): number | undefined {
  const raw = getStringFlag(flags, name);
  if (!raw) {
    return undefined;
  }
  const value = Number(raw);
  if (!Number.isFinite(value)) {
    throw new Error(`--${name} must be a number`);
  }
  return value;
}

function getBoolFlag(flags: Map<string, string | boolean>, name: string): boolean {
  return flags.get(name) === true;
}

function parseRecallTraceCommandArgs(args: string): RecallTraceCommandInput {
  const flags = parseFlagArgs(args).flags;
  return {
    turn: getStringFlag(flags, "turn") as "latest" | "all" | undefined,
    traceId: getStringFlag(flags, "trace-id"),
    sessionId: getStringFlag(flags, "session-id"),
    sessionKey: getStringFlag(flags, "session-key"),
    ovSessionId: getStringFlag(flags, "ov-session-id"),
    source: getStringFlag(flags, "source") as RecallTraceSource | undefined,
    resourceTypes: getStringFlag(flags, "resource-types"),
    since: getNumberFlag(flags, "since"),
    until: getNumberFlag(flags, "until"),
    includeContent: getBoolFlag(flags, "include-content"),
    limit: getNumberFlag(flags, "limit"),
  };
}

function toCommandResult(result: OpenVikingCommandToolResult): CommandResult {
  return { text: result.content[0]!.text, details: result.details };
}

export function createOpenVikingCommandDefinitions(
  deps: OpenVikingCommandDefinitionsDeps,
): CommandDefinition[] {
  return [
    {
      name: "add-resource",
      description: "Add a resource into OpenViking.",
      acceptsArgs: true,
      handler: async (ctx: PluginCommandContext) => {
        try {
          if (deps.isBypassedSession(ctx)) {
            return toCommandResult(deps.makeBypassedToolResult("add_resource"));
          }
          const session = deps.resolvePluginSessionRouting(ctx);
          const input = deps.parseAddResourceCommandArgs(ctx.args ?? "");
          return toCommandResult(await deps.addResourceOpenViking(input, session.actorPeerId));
        } catch (err) {
          return { text: `OpenViking add resource failed: ${err instanceof Error ? err.message : String(err)}` };
        }
      },
    },
    {
      name: "add-skill",
      description: "Add a skill into OpenViking.",
      acceptsArgs: true,
      handler: async (ctx: PluginCommandContext) => {
        try {
          if (deps.isBypassedSession(ctx)) {
            return toCommandResult(deps.makeBypassedToolResult("add_skill"));
          }
          const session = deps.resolvePluginSessionRouting(ctx);
          const input = deps.parseAddSkillCommandArgs(ctx.args ?? "");
          return toCommandResult(await deps.addSkillOpenViking(input, session.actorPeerId));
        } catch (err) {
          return { text: `OpenViking add skill failed: ${err instanceof Error ? err.message : String(err)}` };
        }
      },
    },
    {
      name: "ov-search",
      description: "Search OpenViking resources and skills.",
      acceptsArgs: true,
      handler: async (ctx: PluginCommandContext) => {
        try {
          if (deps.isBypassedSession(ctx)) {
            return toCommandResult(deps.makeBypassedToolResult("ov_search"));
          }
          const session = deps.resolvePluginSessionRouting(ctx);
          const input = deps.parseOVSearchCommandArgs(ctx.args ?? "");
          return toCommandResult(await deps.searchOpenViking(input, session.actorPeerId, session));
        } catch (err) {
          return { text: `OpenViking search failed: ${err instanceof Error ? err.message : String(err)}` };
        }
      },
    },
    {
      name: "ov-query-config",
      description: "Get or set runtime OpenViking query parameters for the current claw/session.",
      acceptsArgs: true,
      handler: async (ctx: PluginCommandContext) => {
        try {
          return await deps.handleQueryConfigCommand(ctx);
        } catch (err) {
          return { text: `OpenViking query config failed: ${err instanceof Error ? err.message : String(err)}` };
        }
      },
    },
    {
      name: "ov-recall-trace",
      description: "Query OpenViking recall trace records.",
      acceptsArgs: true,
      handler: async (ctx: PluginCommandContext) => {
        try {
          if (deps.isBypassedSession(ctx)) {
            return toCommandResult(deps.makeBypassedToolResult("ov_recall_trace"));
          }
          const session = deps.resolvePluginSessionRouting(ctx);
          const input = parseRecallTraceCommandArgs(ctx.args ?? "");
          const result = await deps.queryRecallTraces(input, session);
          return {
            text: deps.formatRecallTraceText(result),
            details: { count: result.entries.length, lookupLayer: result.lookupLayer, warnings: result.warnings, entries: result.entries },
          };
        } catch (err) {
          return { text: `OpenViking recall trace query failed: ${err instanceof Error ? err.message : String(err)}` };
        }
      },
    },
  ];
}
