// Pure helpers for identifying OpenViking sources in recall metadata and tool calls.

// A viking:// URI stops at whitespace, quotes, brackets, or CJK punctuation.
const URI = /viking:\/\/(?:~|[A-Za-z][\w.-]*)(?:\/[^\s"'<>)\]`,，。；：！？、（）《》“”]*)?/g;

// Top-level directories are navigation targets, not sources an answer consulted.
const DIRS = new Set(["resources", "user", "agent", "session", "memories", "skills", "peers"]);

export function isSource(uri) {
  if (typeof uri !== "string" || uri.endsWith("/")) return false;
  const parts = uri.slice("viking://".length).split("/").filter(Boolean);
  return parts.length >= 2 && !DIRS.has(parts.at(-1) || "");
}

// The `ov` CLI can wrap a long URI onto the next line at the same indentation.
export function urisIn(text) {
  const lines = String(text || "").split("\n");
  const found = [];
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i] || "";
    const whole = /^(\s*)(viking:\/\/\S+)\s*$/.exec(line);
    const next = lines[i + 1] || "";
    if (whole && !/\.\w{1,5}$/.test(whole[2] || "")) {
      const tail = new RegExp(`^${whole[1]}(\\S+\\.\\w{1,5})\\s*$`).exec(next);
      if (tail && !/^[#*-]/.test(tail[1] || "")) {
        found.push(`${whole[2]}${tail[1]}`);
        i += 1;
        continue;
      }
    }
    found.push(...(line.match(URI) || []));
  }
  return [...new Set(found)].filter(isSource);
}

// What auto-recall injected: <memory uri score> items, or digest bullets.
export function parseRecall(text) {
  const items = [];
  for (const match of String(text || "").matchAll(/<memory\s([^>]*?)\/?>/g)) {
    const uri = /uri="([^"]+)"/.exec(match[1] || "")?.[1];
    if (uri) {
      items.push({
        uri,
        score: Number(/score="([\d.]+)"/.exec(match[1] || "")?.[1] || 0),
      });
    }
  }
  if (!items.length) {
    for (const line of String(text || "").split("\n")) {
      const uri = /^\s*-/.test(line) ? line.match(URI)?.[0] : undefined;
      if (uri) items.push({ uri, score: 0 });
    }
  }
  const seen = new Set();
  return items.filter((item) => {
    if (!isSource(item.uri) || seen.has(item.uri)) return false;
    seen.add(item.uri);
    return true;
  });
}

// Search terms may be persisted, so redact common credential forms first.
export function redact(text) {
  return String(text || "")
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
const CLI_READ = new Set(["read", "cat", "abstract", "overview"]);
const CLI_SEARCH = new Set(["find", "search", "grep", "glob", "ls", "tree"]);
const CLI_CALL = /(?:^|[;&|(]\s*)(?:\S+=\S+\s+)*(?:ov|openviking)\s+([a-z][\w-]*)([^;&|\n]*)/g;

// Wrapper source only identifies a possible lookup, not an executed read.
// Count source URIs from successful output; never infer reads from arguments.
function classifyWrapped(source) {
  const masked = source.replace(/"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|\/\/[^\n]*|\/\*[\s\S]*?\*\//g,
    (part) => " ".repeat(part.length));
  const pattern = /\btools\.(mcp__\w*openviking\w*__\w+)\s*\(/g;
  for (const match of masked.matchAll(pattern)) {
    const method = MCP_TOOL.exec(match[1])?.[1];
    if (method === "read" || MCP_SEARCH.has(method)) {
      return { opened: [], query: "OpenViking lookup through functions.exec" };
    }
  }
  return null;
}

export function classifyCall(tool, input = {}) {
  if (["functions.exec", "exec"].includes(tool)) {
    const source = typeof input === "string" ? input : input.code ?? input.source ?? "";
    return typeof source === "string" ? classifyWrapped(source) : null;
  }
  const mcp = MCP_TOOL.exec(String(tool || ""))?.[1];
  if (mcp === "read") {
    const raw = Array.isArray(input.uris)
      ? input.uris.join(" ")
      : String(input.uri ?? input.uris ?? "");
    return { opened: (raw.match(URI) || []).filter(isSource), query: null };
  }
  if (mcp && MCP_SEARCH.has(mcp)) {
    const query = input.query ?? input.pattern ?? input.uri ?? input.path ?? "";
    return { opened: [], query: redact(String(query)).slice(0, 80) };
  }
  if (tool !== "Bash" || typeof input.command !== "string") return null;
  const opened = [];
  let query = null;
  for (const match of input.command.matchAll(CLI_CALL)) {
    const subcommand = match[1] || "";
    const rest = match[2] || "";
    if (CLI_READ.has(subcommand)) {
      opened.push(...(rest.match(URI) || []).filter(isSource));
    } else if (CLI_SEARCH.has(subcommand) && query === null) {
      const quoted = /["']([^"']+)["']/.exec(rest)?.[1];
      const words = rest
        .replace(/\s-{1,2}\w[\w-]*(?:[=\s]\S+)?/g, " ")
        .replace(/\s\d?>.*$/, "");
      query = redact((quoted ?? words).trim()).slice(0, 80);
    }
  }
  return opened.length || query !== null
    ? { opened: [...new Set(opened)], query }
    : null;
}

function resultFailed(response) {
  return response.isError === true || response.is_error === true ||
    ["exit_code", "exitCode"].some(
      (key) => typeof response[key] === "number" && response[key] !== 0,
    );
}

// A wrapper can emit several independent results. Exclude failed result blocks
// without discarding successful siblings. Opaque output cannot prove a read.
export function successfulResponseText(response, depth = 0) {
  if (depth > 8) return "";
  if (typeof response === "string") {
    try {
      return successfulResponseText(JSON.parse(response), depth + 1);
    } catch {
      return response;
    }
  }
  if (!response || typeof response !== "object") return "";
  if (Array.isArray(response)) {
    return response.map((part) => successfulResponseText(part, depth + 1)).join("\n");
  }
  if (resultFailed(response)) return "";
  if (Array.isArray(response.content)) {
    return response.content.filter((part) => part?.type === "text")
      .map((part) => successfulResponseText(part.text, depth + 1)).join("\n");
  }
  return Object.values(response).map((part) => successfulResponseText(part, depth + 1)).join("\n");
}

export function toolResponseFailed(response, depth = 0) {
  if (depth > 4) return false;
  if (typeof response === "string") {
    try { return toolResponseFailed(JSON.parse(response), depth + 1); } catch { return false; }
  }
  if (!response || typeof response !== "object") return false;
  if (resultFailed(response)) return true;
  return Array.isArray(response.content) && response.content.some(
    (part) => part?.type === "text" && toolResponseFailed(part.text, depth + 1),
  );
}

export function groupOf(uri) {
  if (/^viking:\/\/user\/[^/]+\/memories\/(preferences|profile|identity|soul)/.test(uri)) {
    return "prefs";
  }
  if (/\/memories\/events\//.test(uri)) return "history";
  if (/\/skills?\//.test(uri)) return "skill";
  if (/^viking:\/\/user\/[^/]+\/(memories|peers)\//.test(uri)) return "work";
  return "docs";
}

export const ICON = {
  prefs: "★",
  history: "◷",
  work: "◆",
  docs: "▤",
  skill: "⚙",
};

const NOUN = {
  prefs: ["preference", "preferences"],
  history: ["past event", "past events"],
  work: ["work memory", "work memories"],
  docs: ["team doc", "team docs"],
  skill: ["skill", "skills"],
};

const plural = (count, [one, many]) => `${count} ${count === 1 ? one : many}`;

export function titleOf(uri) {
  let decoded = uri;
  try { decoded = decodeURIComponent(uri); } catch { /* literal percent in source titles */ }
  const parts = decoded.split("/").filter(Boolean);
  let name = (parts.at(-1) || uri).replace(/\.md$/, "");
  if (/^(\.|summary$|index$|prompts$)/i.test(name)) name = `${parts.at(-2) || ""}/${name}`;
  const date = /\/(\d{4})\/(\d{2})\/(\d{2})\//.exec(uri);
  return date ? `${Number(date[2])}/${Number(date[3])} ${name}` : name;
}

export function consulted(turn = {}) {
  const lookups = Array.isArray(turn.lookups) ? turn.lookups : [];
  const recalled = Array.isArray(turn.recalled) ? turn.recalled : [];
  const opened = new Set(lookups.flatMap((lookup) => (lookup.isError ? [] : lookup.opened || [])));
  const rows = new Map();
  for (const item of recalled) {
    rows.set(item.uri, { uri: item.uri, from: "recall", score: item.score || 0, isOpened: false });
  }
  for (const lookup of lookups) {
    if (lookup.isError) continue;
    for (const uri of [...(lookup.opened || []), ...(lookup.found || [])]) {
      if (!rows.has(uri)) rows.set(uri, { uri, from: "lookup", score: 0, isOpened: false });
    }
  }
  for (const row of rows.values()) row.isOpened = opened.has(row.uri);
  const list = [...rows.values()].sort(
    (a, b) => Number(b.isOpened) - Number(a.isOpened) || b.score - a.score,
  );
  const byGroup = { prefs: 0, history: 0, work: 0, docs: 0, skill: 0 };
  for (const row of list) byGroup[groupOf(row.uri)] += 1;
  return { rows: list, byGroup, readCount: list.filter((row) => row.isOpened).length };
}

export function summaryLine(result) {
  const groups = Object.keys(NOUN)
    .filter((group) => result.byGroup[group] > 0)
    .map((group) => plural(result.byGroup[group], NOUN[group]));
  return [
    "OpenViking",
    plural(result.rows.length, ["source", "sources"]),
    ...groups,
    result.readCount ? `${result.readCount} read` : "",
  ].filter(Boolean).join(" · ");
}

export function expandedLines(turn, result = consulted(turn)) {
  const lines = [summaryLine(result)];
  for (const row of result.rows) {
    const detail = row.from === "recall"
      ? `auto-recalled${row.score > 0 ? ` ${row.score.toFixed(2)}` : ""}`
      : "found by Codex";
    lines.push(`  ${ICON[groupOf(row.uri)]} ${titleOf(row.uri)} · ${detail}${row.isOpened ? " · read" : ""}\n    ${row.uri}`);
  }
  if ((turn.lookups || []).length) lines.push("Codex lookups");
  for (const lookup of turn.lookups || []) {
    const parts = [];
    if (lookup.query !== null) parts.push(`⌕ Searched “${lookup.query}” · ${(lookup.found || []).length} results`);
    if ((lookup.opened || []).length) parts.push(`▤ Read ${(lookup.opened || []).map(titleOf).join(", ")}`);
    if (lookup.isError) parts.push("failed");
    if (parts.length) lines.push(`  ${parts.join(" · ")}`);
  }
  return lines;
}
