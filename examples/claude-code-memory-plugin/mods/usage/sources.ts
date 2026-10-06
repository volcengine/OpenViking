// Pure helpers: which viking:// URIs a recall block or a tool call names, which
// tool calls are OpenViking lookups, and what one answer consulted.

import type { Lookup, Turn } from "./types";

// A viking:// URI stops at whitespace, quotes, brackets or CJK punctuation.
const URI = /viking:\/\/(?:~|[A-Za-z][\w.-]*)(?:\/[^\s"'<>)\]`,，。；：！？、（）《》“”]*)?/g;

// Top-level directories: a URI ending in one of them is a directory, not a source.
const DIRS = new Set(["resources", "user", "agent", "session", "memories", "skills", "peers"]);

// A URI that names something an answer could draw on, not a scope or directory.
export function isSource(uri: string): boolean {
  if (uri.endsWith("/")) return false;
  const parts = uri.slice("viking://".length).split("/").filter(Boolean);
  return parts.length >= 2 && !DIRS.has(parts.at(-1) ?? "");
}

// The source URIs a tool's output names, in order. The `ov` CLI wraps a long URI
// onto the next line at the same indent; that continuation is joined back.
export function urisIn(text: string): string[] {
  const lines = text.split("\n");
  const found: string[] = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i] ?? "";
    const whole = /^(\s*)(viking:\/\/\S+)\s*$/.exec(line);
    const next = lines[i + 1] ?? "";
    if (whole && !/\.\w{1,5}$/.test(whole[2] ?? "")) {
      const tail = new RegExp(`^${whole[1]}(\\S+\\.\\w{1,5})\\s*$`).exec(next);
      if (tail && !/^[#*-]/.test(tail[1] ?? "")) {
        found.push(`${whole[2]}${tail[1]}`);
        i++;
        continue;
      }
    }
    found.push(...(line.match(URI) ?? []));
  }
  return [...new Set(found)].filter(isSource);
}

export type Recalled = { uri: string; score: number };

// What auto-recall injected for a prompt: <memory uri score> items, or the
// digest form's "- summary 来源：viking://..." lines.
export function parseRecall(text: string): Recalled[] {
  const items: Recalled[] = [];
  for (const m of text.matchAll(/<memory\s([^>]*?)\/?>/g)) {
    const uri = /uri="([^"]+)"/.exec(m[1] ?? "")?.[1];
    if (uri) items.push({ uri, score: Number(/score="([\d.]+)"/.exec(m[1] ?? "")?.[1] ?? 0) });
  }
  if (!items.length) {
    for (const line of text.split("\n")) {
      const uri = /^\s*-/.test(line) ? line.match(URI)?.[0] : undefined;
      if (uri) items.push({ uri, score: 0 });
    }
  }
  const seen = new Set<string>();
  return items.filter((it) => isSource(it.uri) && !seen.has(it.uri) && !!seen.add(it.uri));
}

// Secrets never reach storage: JWT-like tokens, Bearer headers, sk- keys, key=value.
export function redact(text: string): string {
  return text
    .replace(/[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{40,}/g, "[REDACTED]")
    .replace(/Bearer\s+[A-Za-z0-9._~+/-]{16,}/gi, "Bearer [REDACTED]")
    .replace(/sk-[A-Za-z0-9_-]{20,}/g, "[REDACTED]")
    .replace(
      /((?:api[_-]?key|token|secret|password|authorization)["']?\s*[:=]\s*["']?|--(?:api-key|token|password)[=\s]+)[^\s"',;&|]+/gi,
      "$1[REDACTED]",
    );
}

const MCP_TOOL = /^mcp__.*openviking.*__(\w+)$/;
const MCP_SEARCH = new Set(["search", "find", "grep", "glob", "list", "tree"]);

// `ov` CLI subcommands that open the files they name, and those that list matches.
const CLI_READ = new Set(["read", "cat", "abstract", "overview"]);
const CLI_SEARCH = new Set(["find", "search", "grep", "glob", "ls", "tree"]);

// One `ov` / `openviking` invocation at the start of a command or after ; & | (
const CLI_CALL = /(?:^|[;&|(]\s*)(?:\S+=\S+\s+)*(?:ov|openviking)\s+([a-z][\w-]*)([^;&|\n]*)/g;

// How one tool call reads OpenViking: the URIs it opens in full, and what it
// searched for. Null when the call is not an OpenViking lookup (writes and
// status checks included). A shell command itself is never kept.
export function classifyCall(
  tool: string,
  input: Record<string, unknown>,
): { opened: string[]; query: string | null } | null {
  const mcp = MCP_TOOL.exec(tool)?.[1];
  if (mcp === "read") {
    const raw = Array.isArray(input.uris)
      ? input.uris.join(" ")
      : String(input.uri ?? input.uris ?? "");
    return { opened: (raw.match(URI) ?? []).filter(isSource), query: null };
  }
  if (mcp && MCP_SEARCH.has(mcp)) {
    const q = input.query ?? input.pattern ?? input.uri ?? input.path ?? "";
    return { opened: [], query: redact(String(q)).slice(0, 80) };
  }
  if (tool !== "Bash" || typeof input.command !== "string") return null;
  const opened: string[] = [];
  let query: string | null = null;
  for (const m of input.command.matchAll(CLI_CALL)) {
    const sub = m[1] ?? "";
    const rest = m[2] ?? "";
    if (CLI_READ.has(sub)) opened.push(...(rest.match(URI) ?? []).filter(isSource));
    else if (CLI_SEARCH.has(sub) && query === null) {
      const quoted = /["']([^"']+)["']/.exec(rest)?.[1];
      const words = rest.replace(/\s-{1,2}\w[\w-]*(?:[=\s]\S+)?/g, " ").replace(/\s\d?>.*$/, "");
      query = redact((quoted ?? words).trim()).slice(0, 80);
    }
  }
  return opened.length || query !== null ? { opened: [...new Set(opened)], query } : null;
}

export type Group = "prefs" | "history" | "work" | "docs" | "skill";

// What a source is to the person, from where it lives.
export function groupOf(uri: string): Group {
  if (/^viking:\/\/user\/[^/]+\/memories\/(preferences|profile|identity|soul)/.test(uri))
    return "prefs";
  if (/\/memories\/events\//.test(uri)) return "history";
  if (/\/skills?\//.test(uri)) return "skill";
  if (/^viking:\/\/user\/[^/]+\/(memories|peers)\//.test(uri)) return "work";
  return "docs";
}

export const ICON: Record<Group, string> = {
  prefs: "★",
  history: "◷",
  work: "◆",
  docs: "▤",
  skill: "⚙",
};

const NOUN: Record<Group, [string, string]> = {
  prefs: ["preference", "preferences"],
  history: ["past event", "past events"],
  work: ["work memory", "work memories"],
  docs: ["team doc", "team docs"],
  skill: ["skill", "skills"],
};

const plural = (n: number, [one, many]: [string, string]) => `${n} ${n === 1 ? one : many}`;

// URIs can hold a literal "%" (a title like "50% off"), which decodeURIComponent rejects.
function decodeUri(uri: string): string {
  try {
    return decodeURIComponent(uri);
  } catch {
    return uri;
  }
}

// A short name for a source: its file name, with the date of a dated event.
export function titleOf(uri: string): string {
  const parts = decodeUri(uri).split("/").filter(Boolean);
  let name = (parts.at(-1) ?? uri).replace(/\.md$/, "");
  if (/^(\.|summary$|index$|prompts$)/i.test(name)) name = `${parts.at(-2) ?? ""}/${name}`;
  const d = /\/(\d{4})\/(\d{2})\/(\d{2})\//.exec(uri);
  return d ? `${Number(d[2])}/${Number(d[3])} ${name}` : name;
}

export type Row = { uri: string; from: "recall" | "lookup"; score: number; isOpened: boolean };

// What one answer consulted: auto-recall's items and what Claude's own reads and
// searches returned, files Claude opened first, then by recall score.
export function consulted(turn: Turn) {
  const opened = new Set(turn.lookups.flatMap((l: Lookup) => (l.isError ? [] : l.opened)));
  const rows = new Map<string, Row>();
  for (const it of turn.recalled)
    rows.set(it.uri, { uri: it.uri, from: "recall", score: it.score, isOpened: false });
  for (const l of turn.lookups) {
    if (l.isError) continue;
    for (const uri of [...l.opened, ...l.found]) {
      if (!rows.has(uri)) rows.set(uri, { uri, from: "lookup", score: 0, isOpened: false });
    }
  }
  for (const r of rows.values()) r.isOpened = opened.has(r.uri);
  const list = [...rows.values()].sort(
    (a, b) => Number(b.isOpened) - Number(a.isOpened) || b.score - a.score,
  );
  const byGroup: Record<Group, number> = { prefs: 0, history: 0, work: 0, docs: 0, skill: 0 };
  for (const r of list) byGroup[groupOf(r.uri)] += 1;
  return { rows: list, byGroup, readInFull: list.filter((r) => r.isOpened).length };
}

// The collapsed card: "OV · 8 sources · 4 past events · 3 work memories · 1 read in full".
export function summaryLine(c: ReturnType<typeof consulted>): string {
  const groups = (Object.keys(NOUN) as Group[])
    .filter((g) => c.byGroup[g] > 0)
    .map((g) => plural(c.byGroup[g], NOUN[g]));
  return [
    "OV",
    plural(c.rows.length, ["source", "sources"]),
    ...groups,
    c.readInFull ? `${c.readInFull} read in full` : "",
  ]
    .filter(Boolean)
    .join(" · ");
}
