// What a source is to the person: its kind, group and relevance, and what one
// answer consulted.

import type { Lookup, RecallItem, Turn, TurnLookup } from "../types";
import type { Strings } from "./strings";
import { IS_URI } from "./parse";

const OV_TOOL = /^mcp__.*openviking.*__(\w+)$/;

const WRITE_TOOLS = new Set(["remember", "write", "edit", "add_resource", "add_skill", "forget"]);

// An `ov` or `openviking` CLI invocation at the start of a command or after ; && | (
const OV_CLI = /(?:^|[;&|(]\s*|\s&&\s*)(?:\S+=\S+\s+)*(?:ov|openviking)\s+(?!-)\S/;

// `ov` subcommands that change what OpenViking holds.
const OV_CLI_WRITE = /\bov\s+(add|write|remember|rm|mv|edit)\b/;

export type ToolCallKind = { name: string; kind: "read" | "write"; isCli: boolean };

// Whether a tool call is one of Claude's own OpenViking lookups or writes: a tool
// of an OpenViking MCP server, or an `ov` / `openviking` CLI call through Bash. A
// command that only mentions a viking:// path (writing docs, grepping) is not one.
export function classifyToolCall(tool: string, command: string): ToolCallKind | null {
  const mcp = OV_TOOL.exec(tool)?.[1];
  if (mcp) return { name: mcp, kind: WRITE_TOOLS.has(mcp) ? "write" : "read", isCli: false };
  if (tool === "Bash" && OV_CLI.test(command)) {
    return { name: "ov cli", kind: OV_CLI_WRITE.test(command) ? "write" : "read", isCli: true };
  }
  return null;
}

// Tools that open the files they name; the rest return a list of matches.
export const OPEN_TOOLS = new Set(["read", "ov cli"]);

// Status checks name no memory, so they are not something an answer referred to.
export const QUIET_TOOLS = new Set(["health"]);

export function isExpanded(uri: string, lookups: Lookup[]) {
  return lookups.some((l) => l.kind === "read" && l.target.includes(uri));
}

type Kind = { icon: string; label: keyof Strings["kinds"]; color: string; personal: boolean };

// What a memory is to the person, from where it lives.
export function kindOf(uri: string): Kind {
  if (/^viking:\/\/user\/[^/]+\/memories\/(preferences|profile|identity|soul)/.test(uri))
    return { icon: "★", label: "preference", color: "blue", personal: true };
  if (/^viking:\/\/user\/[^/]+\/memories\/events\//.test(uri))
    return { icon: "◷", label: "history", color: "blue", personal: true };
  if (/^viking:\/\/user\/[^/]+\/memories\/experiences\//.test(uri))
    return { icon: "◆", label: "lesson", color: "blue", personal: true };
  if (/^viking:\/\/user\/[^/]+\/memories\//.test(uri))
    return { icon: "◆", label: "notes", color: "blue", personal: true };
  if (/^viking:\/\/user\/[^/]+\/peers\//.test(uri))
    return { icon: "⇄", label: "agents", color: "blue", personal: true };
  if (/\/skills?\//.test(uri)) return { icon: "⚙", label: "skill", color: "blue", personal: false };
  return { icon: "▤", label: "docs", color: "gray", personal: false };
}

// Which group a source counts toward in the answer heading: your preferences,
// your history (dated events), your work memory (notes, lessons, your agents'
// memories), team docs, or skills.
export type Category = "prefs" | "history" | "work" | "docs" | "skill";

const CATEGORY: Record<Kind["label"], Category> = {
  preference: "prefs",
  history: "history",
  lesson: "work",
  notes: "work",
  agents: "work",
  skill: "skill",
  docs: "docs",
};

function categoryOf(uri: string): Category {
  return CATEGORY[kindOf(uri).label];
}

const EMPTY =
  /empty (document|markdown)|no (actual|clear|meaningful) content|no clear primary purpose|无实际|空白|无任何可识别|乱码|无任何有效|没有实际内容/i;

export function isEmptyDoc(it: RecallItem) {
  return EMPTY.test(it.summary);
}

// Listed as relevant only above a score bar, your own memories included, so
// the pane never claims more than recall delivered. Digest items carry no
// score; for those your own memories count.
const RELEVANT_SCORE = 0.55;

// Your own memories (preferences, history, notes) score lower for the same
// usefulness, so they get a lower bar.
const RELEVANT_SCORE_PERSONAL = 0.5;

export function isStrong(it: RecallItem) {
  if (EMPTY.test(it.summary)) return false;
  const personal = kindOf(it.uri).personal;
  if (it.score === 0) return personal;
  return it.score >= (personal ? RELEVANT_SCORE_PERSONAL : RELEVANT_SCORE);
}

export function titleOf(uri: string): string | null {
  const name = decodeURIComponent(uri.split("/").filter(Boolean).at(-1) ?? "").replace(/\.md$/, "");
  if (
    !name ||
    name.startsWith(".") ||
    /^(prompts|summary|index|n_\d+)$|\.aiff|^[0-9a-f-]{12,}$|conversation_/i.test(name)
  )
    return null;
  return name.replace(/[_-]+/g, " ");
}

export function dateOf(uri: string): string {
  const m = /\/(\d{4})\/(\d{2})\/(\d{2})\//.exec(uri);
  return m ? `${Number(m[2])}/${Number(m[3])} ` : "";
}

export function gist(summary: string): string {
  const s = summary
    .replace(/^#\s*Summary\s*/i, "")
    .replace(/^(#{1,6}\s*[^#]*?\s+)+(?=[-*\d]|$)/, "")
    .replace(/^#+\s*/, "")
    .replace(/^\s*(\d+[.、)]|[-*])\s*/, "")
    .replace(/\s#+\s[\s\S]*$/, "")
    .replace(
      /^(本文档|该文档|这是|This (document|file) (is|records))(的)?(核心|主要)?(目的)?(是|为)?[：:,，]?\s*/i,
      "",
    )
    .trim();
  return (s.split(/(?<=[。！？.!?])\s*/)[0] ?? s).slice(0, 90);
}

export function shortName(uri: string): string {
  return titleOf(uri) ?? decodeURIComponent(uri.split("/").slice(-2).join("/"));
}

// One of Claude's own lookups as a row reads: what it did, and to what.
export function describeLookup(t: Strings, l: TurnLookup) {
  const isOpen = OPEN_TOOLS.has(l.tool);
  const isWrite = l.kind === "write";
  return {
    icon: isWrite ? "✎" : isOpen ? "▤" : "⌕",
    verb: isWrite ? t.lookupWrite : isOpen ? t.lookupRead : t.lookupSearch,
    what: isOpen || isWrite ? l.uris.map(shortName).join(", ") || l.query : `“${l.query}”`,
    count: !isOpen && !isWrite && !l.isError ? t.results(l.uris.length) : null,
  };
}

// What OpenViking put in front of Claude for one answer: the memories recalled
// for the prompt (unless auto-recall is off) and what Claude's own reads and
// searches returned. Nothing is guessed from the answer's wording.
export function consultedOf(turn: Turn, recallHidden: boolean) {
  const byScore = (a: RecallItem, b: RecallItem) => b.score - a.score;
  const items = recallHidden ? [] : turn.items.filter((it) => !isEmptyDoc(it));
  const recalled = new Set(items.map((it) => it.uri));
  // Relevance of what Claude found: a file it chose to open ranks first, a search
  // result by the score the search gave it.
  const relevance = new Map<string, number>();
  for (const l of turn.lookups) {
    if (l.isError || l.kind !== "read") continue;
    for (const u of l.uris) {
      if (!IS_URI.test(u) || recalled.has(u)) continue;
      const r = OPEN_TOOLS.has(l.tool) ? 1 : (l.scores?.[u] ?? 0);
      relevance.set(u, Math.max(relevance.get(u) ?? 0, r));
    }
  }
  const found = [...relevance.keys()];
  const strong = items.filter((it) => isStrong(it)).sort(byScore);
  const weak = items.filter((it) => !isStrong(it)).sort(byScore);
  // Everything consulted, most relevant first.
  const ranked = [
    ...items.map((it) => ({
      uri: it.uri,
      from: "recall" as const,
      score: it.score,
      isWeak: !isStrong(it),
    })),
    ...found.map((u) => ({
      uri: u,
      from: "lookup" as const,
      score: relevance.get(u) ?? 0,
      isWeak: false,
    })),
  ].sort((a, b) => Number(a.isWeak) - Number(b.isWeak) || b.score - a.score);
  // Of what was consulted: how much is about the person, and how much Claude
  // opened in full (a read, not just a search hit or a recalled summary).
  const opened = new Set(
    turn.lookups.filter((l) => !l.isError && OPEN_TOOLS.has(l.tool)).flatMap((l) => l.uris),
  );
  const all = ranked.map((r) => r.uri);
  const byCategory = { prefs: 0, history: 0, work: 0, docs: 0, skill: 0 };
  for (const u of all) byCategory[categoryOf(u)] += 1;
  const openedFull = all.filter((u) => opened.has(u)).length;
  return {
    strong,
    weak,
    found,
    ranked,
    byCategory,
    openedFull,
    total: strong.length + weak.length + found.length,
  };
}

// "1 preference · 1 from your history · 6 work memory · 3 team docs · 1 skill · 2 opened in full", zero groups left out.
export function breakdown(
  t: Strings,
  c: { byCategory: Record<Category, number>; openedFull: number },
): string {
  const parts = [
    c.byCategory.prefs ? t.catPrefs(c.byCategory.prefs) : "",
    c.byCategory.history ? t.catHistory(c.byCategory.history) : "",
    c.byCategory.work ? t.catWork(c.byCategory.work) : "",
    c.byCategory.docs ? t.catDocs(c.byCategory.docs) : "",
    c.byCategory.skill ? t.catSkills(c.byCategory.skill) : "",
    c.openedFull ? t.openedFull(c.openedFull) : "",
  ].filter(Boolean);
  return parts.length ? ` · ${parts.join(" · ")}` : "";
}

export function consultedUris(turn: Turn, recallHidden: boolean): string[] {
  const c = consultedOf(turn, recallHidden);
  return [...c.strong, ...c.weak].map((it) => it.uri).concat(c.found);
}
