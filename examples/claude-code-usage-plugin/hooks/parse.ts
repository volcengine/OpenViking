// Pure parsing of what OpenViking and Claude Code hand the plugin: recall blocks,
// the startup context, viking:// URIs, secrets to redact, and the text
// fingerprints that match an answer to its row in the transcript.

import type { Group, Inject, RecallItem, Reply } from "../types";

// A viking:// URI starts with an ASCII scope (user, resources, agent, ~) and
// stops at whitespace or punctuation, CJK included. Prose that merely mentions
// "viking://格式的来源URI" in a summary is not a URI.
export const URI =
  /viking:\/\/(?:~|[A-Za-z][\w.-]*)(?:\/[^\s"'<>)\]`,，。；：！？、（）《》“”]*)?/g;

// A whole URI as stored: file names may contain spaces, never line breaks or CJK punctuation.
export const IS_URI =
  /^viking:\/\/(?:~|[A-Za-z][\w.-]*)(?:\/[^\n"'<>)\]`,，。；：！？、（）《》“”]*)?$/;

// A line that is only a URI, after an optional search-result prefix or inside
// "=== … ===": file names there can contain spaces.
export const URI_LINE =
  /^\s*(?:-\s*\[[\w-]+\s+\d{1,3}%\]\s+|===\s+)?(viking:\/\/(?:~|[A-Za-z][\w.-]*)\/[^"'<>`]*?)\s*(?:===)?\s*$/;

// The viking:// URIs a tool's output names: whole-line URIs first (spaces
// allowed), then any others inline.
export function urisIn(text: string): string[] {
  const found: string[] = [];
  for (const line of text.split("\n")) {
    const m = URI_LINE.exec(line);
    if (m?.[1]) found.push(m[1]);
  }
  for (const u of text.match(URI) ?? []) {
    if (!found.some((f) => f === u || f.startsWith(`${u} `))) found.push(u);
  }
  return [...new Set(found)];
}

const LAYER: Record<string, RecallItem["layer"]> = {
  abstract: "L0",
  overview: "L1",
  full: "L2",
  uri: "URI",
};

// Rough token estimate: CJK ≈ 1 token per char, everything else ≈ 4 chars per token.
function estTokens(text: string) {
  const cjk = (text.match(/[　-鿿＀-￯]/g) ?? []).length;
  return Math.max(1, Math.round(cjk + (text.length - cjk) / 4));
}

// The startup <openviking-context>: profile + memory tree.
export function parseStartup(text: string, at: string): Inject | null {
  const start = text.indexOf("<openviking-context");
  if (start < 0) return null;
  const body = text.slice(start);
  const source = /source="([^"]+)"/.exec(body)?.[1] ?? "startup";
  const prof = /<user-profile[^>]*>([\s\S]*?)<\/user-profile>/.exec(body)?.[1] ?? "";
  const profile = prof
    .split("\n")
    .map((l) =>
      l
        .replace(/^[-#\s]+/, "")
        .replace(/（as of [^）]+）/, "")
        .trim(),
    )
    .filter(Boolean);
  const groups: Group[] = [];
  for (const line of body.split("\n")) {
    const dir = /^\s*(viking:\/\/\S+\/)\s*$/.exec(line)?.[1];
    if (dir) groups.push({ dir, files: [] });
    const file = /^\s+-\s+(.+)$/.exec(line)?.[1];
    if (file) groups.at(-1)?.files.push(file.trim());
  }
  if (!profile.length && !groups.length) return null;
  return { at, source, profile, groups };
}

// A per-prompt recall: <memory uri score detail> items, or a server-assembled digest.
export function parseRecall(text: string): Omit<RecallItem, "firstTurn">[] {
  const items: Omit<RecallItem, "firstTurn">[] = [];
  for (const m of text.matchAll(/<memory\s([^>]*?)(?:\/>|>([\s\S]*?)<\/memory>)/g)) {
    const attrs = m[1] ?? "";
    const body = (m[2] ?? "")
      .split("\n")
      .filter((l) => !/^\s*#{1,6}\s/.test(l))
      .join("\n")
      .trim();
    const uri = /uri="([^"]+)"/.exec(attrs)?.[1];
    if (!uri) continue;
    const detail = /detail="(\w+)"/.exec(attrs)?.[1] ?? "abstract";
    items.push({
      uri,
      layer: LAYER[detail] ?? "L0",
      score: Number(/score="([\d.]+)"/.exec(attrs)?.[1] ?? 0),
      tokens: estTokens(m[0]),
      summary: body.replace(/\s+/g, " ").slice(0, 160),
    });
  }
  if (items.length) return items;
  // Digest form: "- summary 来源：viking://..."
  for (const line of text.split("\n")) {
    const uri = line.match(URI)?.[0];
    if (!uri || !/^\s*-/.test(line)) continue;
    const summary = line.replace(/^\s*-\s*/, "").replace(/\s*(来源|source)[:：]\s*\S+/i, "");
    const same = items.find((it) => it.uri === uri);
    if (same) {
      same.tokens += estTokens(line);
      same.summary = `${same.summary} / ${summary}`.slice(0, 160);
      continue;
    }
    items.push({
      uri,
      layer: "L0",
      score: 0,
      tokens: estTokens(line),
      summary: summary.slice(0, 160),
    });
  }
  return items;
}

// Secrets never reach storage or the pane: JWT-like tokens, Bearer headers,
// sk- keys, and key=value / --api-key style arguments.
export function redact(text: string): string {
  return text
    .replace(/[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{40,}/g, "[REDACTED]")
    .replace(/Bearer\s+[A-Za-z0-9._~+/-]{16,}/gi, "Bearer [REDACTED]")
    .replace(/sk-[A-Za-z0-9_-]{20,}/g, "[REDACTED]")
    .replace(
      /((?:api[_-]?key|token|secret|password|passwd|authorization)["']?\s*[:=]\s*["']?|--(?:api-key|token|password)[=\s]+)[^\s"',;&|]+/gi,
      "$1[REDACTED]",
    );
}

export function stripMuted(contexts: readonly string[] | undefined, muted: string[]) {
  let hits = 0;
  if (!contexts || !muted.length) return { contexts, hits };
  const out = contexts.map((c) =>
    c
      .replace(
        /<memory\s[^>]*?uri="([^"]+)"[^>]*?(?:\/>|>[\s\S]*?<\/memory>)\n?/g,
        (whole, uri: string) => {
          if (!muted.includes(uri)) return whole;
          hits += 1;
          return "";
        },
      )
      // Digest form: one "- summary 来源：viking://..." line per item.
      .split("\n")
      .filter((line) => {
        const uri = /^\s*-/.test(line) ? line.match(URI)?.[0] : undefined;
        if (!uri || !muted.includes(uri)) return true;
        hits += 1;
        return false;
      })
      .join("\n"),
  );
  return { contexts: out, hits };
}

// A profile line that states a role (职业 / Role / Job title …).
export const ROLE_LINE =
  /^(?:职业|角色|岗位|职位|职务|role|job(?:\s+title)?|title|occupation|position)\s*[：:]\s*(.+)$/i;

// A fingerprint of row text, so cards can be matched to rows without keeping
// any of the prompt's or answer's words (FNV-1a, 32-bit).
export function fingerprint(text: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return `${text.length}:${h.toString(16)}`;
}

// Endings fingerprinted per answer, longest first. The last text block of an
// answer can be short (tool calls split an answer into blocks), so its ending
// is matched at the longest of these lengths it can fill.
export const TAILS = [400, 160, 80, 40, 20];

export const HEAD = 200;

// The reply a transcript row draws, by its id, or else by an ending only one answer has.
export function replyFor(replies: Reply[], id: string, text: string): Reply | undefined {
  const byId = replies.find((r) => r.id !== "" && r.id === id);
  if (byId) return byId;
  const end = text.trimEnd();
  // The longest ending this row can fill; one answer must match it, not two.
  const k = TAILS.find((size) => end.length >= size);
  if (k === undefined) return undefined;
  const mark = fingerprint(end.slice(-k));
  const hits = replies.filter((r) => r.tails.includes(mark));
  return hits.length === 1 ? hits[0] : undefined;
}
