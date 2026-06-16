import type { AddResourceResult, AddSkillResult, FindResult, FindResultItem } from "./client.js";
import { clampScore } from "./memory-ranking.js";
import type { RecallTraceEntry } from "./recall-trace.js";

export function formatRecallTraceText(result: {
  entries: RecallTraceEntry[];
  lookupLayer: string;
  warnings: string[];
}): string {
  if (result.entries.length === 0) {
    return `No OpenViking recall traces found (lookupLayer=${result.lookupLayer}).`;
  }
  const blocks = result.entries.map((entry, index) => {
    const selected = entry.selected.slice(0, 8)
      .map((item) => `  - ${item.uri}${item.score !== undefined ? ` (${(clampScore(item.score) * 100).toFixed(0)}%)` : ""}`)
      .join("\n");
    return [
      `## Trace ${index + 1}: ${entry.source}`,
      `traceId: ${entry.traceId}`,
      `query: ${entry.trigger.query}`,
      `resourceTypes: ${entry.resourceTypes.join(", ")}`,
      `searches: ${entry.searches.map((search) => search.contextType ?? search.resourceType).join(", ")}`,
      `stats: candidates=${entry.stats.candidateCount}, selected=${entry.stats.selectedCount}, injected=${entry.stats.injectedCount}`,
      selected ? `selected:\n${selected}` : "selected: (none)",
    ].join("\n");
  });
  const warnings = result.warnings.length > 0
    ? `\n\nWarnings:\n${result.warnings.map((warning) => `- ${warning}`).join("\n")}`
    : "";
  return `${blocks.join("\n\n")}${warnings}`;
}

export function formatResourceImportText(result: AddResourceResult): string {
  const root = result.root_uri ? ` ${result.root_uri}` : "";
  const warnings = result.warnings?.length ? ` Warnings: ${result.warnings.join("; ")}` : "";
  return `Imported OpenViking resource.${root}${warnings}`.trim();
}

export function formatSkillImportText(result: AddSkillResult): string {
  const uri = result.uri ? ` ${result.uri}` : "";
  const name = result.name ? ` (${result.name})` : "";
  return `Imported OpenViking skill${name}.${uri}`.trim();
}

export function mergeFindResults(results: FindResult[]): FindResult {
  const deduplicate = (items: FindResultItem[]): FindResultItem[] => {
    const seen = new Map<string, FindResultItem>();
    for (const item of items) {
      if (!seen.has(item.uri)) seen.set(item.uri, item);
    }
    return Array.from(seen.values());
  };
  const memories = deduplicate(results.flatMap((result) => result.memories ?? []));
  const resources = deduplicate(results.flatMap((result) => result.resources ?? []));
  const skills = deduplicate(results.flatMap((result) => result.skills ?? []));
  return {
    memories,
    resources,
    skills,
    total: memories.length + resources.length + skills.length,
  };
}

function truncateSummary(value: string, maxChars = 220): string {
  const collapsed = value.replace(/\s+/g, " ").trim();
  if (collapsed.length <= maxChars) return collapsed;
  return `${collapsed.slice(0, maxChars - 3)}...`;
}

export function formatOVSearchRows(result: FindResult): string[] {
  const items = [
    ...(result.memories ?? []).map((item) => ({ contextType: "memory", item })),
    ...(result.resources ?? []).map((item) => ({ contextType: "resource", item })),
    ...(result.skills ?? []).map((item) => ({ contextType: "skill", item })),
  ];
  if (items.length === 0) {
    return [];
  }
  const numberHeader = "no";
  const numberWidth = Math.max(numberHeader.length, String(items.length).length);
  const typeWidth = Math.max("type".length, ...items.map(({ contextType }) => contextType.length));
  const uriWidth = Math.max("uri".length, ...items.map(({ item }) => item.uri.length));
  const levelWidth = Math.max("level".length, ...items.map(({ item }) => String(item.level ?? "").length));
  const scoreWidth = Math.max(
    "score".length,
    ...items.map(({ item }) => (typeof item.score === "number" ? item.score.toFixed(2).length : 0)),
  );
  return [
    `${numberHeader.padEnd(numberWidth)}  ${"type".padEnd(typeWidth)}  ${"uri".padEnd(uriWidth)}  ${"level".padEnd(levelWidth)}  ${"score".padEnd(scoreWidth)}  abstract`,
    ...items.map(({ contextType, item }, index) => {
      const score = typeof item.score === "number" ? item.score.toFixed(2) : "";
      const summary = truncateSummary(item.abstract || item.overview || "(no summary)");
      return `${String(index + 1).padEnd(numberWidth)}  ${contextType.padEnd(typeWidth)}  ${item.uri.padEnd(uriWidth)}  ${String(item.level ?? "").padEnd(levelWidth)}  ${score.padEnd(scoreWidth)}  ${summary}`;
    }),
  ];
}

export function formatOVSearchText(query: string, uri: string | undefined, result: FindResult): string {
  if ((result.total ?? 0) <= 0) {
    const scope = uri ? ` under ${uri}` : "";
    return `No OpenViking resource or skill results found for "${query}"${scope}.`;
  }
  const scope = uri ? ` under ${uri}` : "";
  const lines = [
    `Found ${result.total ?? 0} OpenViking results for "${query}"${scope}`,
    "Tip: search results are ranked snippets. Use ov_read on exact hit URIs before answering precise questions. Use ov_list on a hit's parent URI to inspect sibling chunks or overview files before answering procedural or multi-step questions.",
    "",
    ...formatOVSearchRows(result),
  ].filter((line, index, all) => line || (all[index - 1] && all[index + 1]));
  return lines.join("\n");
}

export function formatOVListEntry(entry: unknown): string {
  if (typeof entry === "string") return entry;
  if (!entry || typeof entry !== "object") return String(entry);
  const item = entry as Record<string, unknown>;
  const uri = typeof item.uri === "string" ? item.uri : "";
  const name = typeof item.name === "string" ? item.name : "";
  const isDir = item.isDir === true || item.type === "directory";
  const marker = isDir ? "[dir]" : "[file]";
  const summary =
    typeof item.abstract === "string" && item.abstract.trim()
      ? item.abstract.trim().replace(/\s+/g, " ")
      : typeof item.overview === "string" && item.overview.trim()
        ? item.overview.trim().replace(/\s+/g, " ")
        : "";
  const label = uri || name || JSON.stringify(item);
  return summary ? `${marker} ${label} - ${summary}` : `${marker} ${label}`;
}

export function formatOVListText(uri: string, entries: unknown[]): string {
  if (entries.length === 0) {
    return `No OpenViking entries found under ${uri}.`;
  }
  return [
    `Listed ${entries.length} OpenViking entr${entries.length === 1 ? "y" : "ies"} under ${uri}`,
    "",
    ...entries.map((entry) => formatOVListEntry(entry)),
  ].join("\n");
}

export function formatOVReadText(uri: string, content: string): string {
  const body = content || "(empty OpenViking content)";
  return [`--- START OF ${uri} ---`, body, `--- END OF ${uri} ---`].join("\n");
}

export function formatOVMultiReadText(
  results: Array<{ uri: string; content: string; success: boolean }>,
): string {
  return [
    `Multi-read results for ${results.length} OpenViking resource${results.length === 1 ? "" : "s"}:`,
    "",
    ...results.flatMap((result) => [
      `--- START OF ${result.uri} ---`,
      result.success ? (result.content || "(empty OpenViking content)") : `ERROR: ${result.content}`,
      `--- END OF ${result.uri} ---`,
      "",
    ]),
  ].join("\n").trimEnd();
}
