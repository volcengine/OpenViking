import type { FindResult, FindResultItem } from "./client.js";
import type {
  RecallResourceType,
  RecallTraceEntry,
  RecallTraceResult,
  RecallTraceSource,
} from "./recall-trace.js";

export function createTraceId(source: RecallTraceSource): string {
  return `${source}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
}

export function previewText(value: string | undefined | null, maxChars: number): string | undefined {
  const normalized = typeof value === "string" ? value.trim().replace(/\s+/g, " ") : "";
  if (!normalized) return undefined;
  return normalized.length > maxChars ? normalized.slice(0, maxChars) : normalized;
}

export function boundTraceQuery(query: string, maxChars: number): { query: string; queryTruncated?: boolean } {
  return query.length <= maxChars
    ? { query }
    : { query: query.slice(0, maxChars), queryTruncated: true };
}

export function inferRecallResourceType(uri: string | undefined): RecallResourceType | undefined {
  if (!uri) return undefined;
  if (uri.startsWith("viking://resources")) return "resource";
  if (uri.startsWith("viking://session/") || uri.includes("/sessions/")) return "session";
  if (uri.startsWith("viking://user/")) return "user";
  return undefined;
}

export function toRecallTraceResult(
  item: FindResultItem,
  resultType: RecallTraceResult["resultType"],
  previewChars: number,
  resourceType: RecallTraceResult["resourceType"] = inferRecallResourceType(item.uri),
): RecallTraceResult {
  return {
    uri: item.uri,
    resourceType,
    category: item.category,
    score: item.score,
    level: item.level,
    abstractPreview: previewText(item.abstract ?? item.overview, previewChars),
    resultType,
  };
}

export function traceResultsFromFindResult(
  result: FindResult,
  previewChars: number,
  limit: number,
  resourceTypes: Partial<Record<"memory" | "resource" | "skill", RecallTraceResult["resourceType"]>> = {},
): RecallTraceResult[] {
  return [
    ...(result.memories ?? []).map((item) => toRecallTraceResult(item, "memory", previewChars, resourceTypes.memory)),
    ...(result.resources ?? []).map((item) => toRecallTraceResult(item, "resource", previewChars, resourceTypes.resource ?? "resource")),
    ...(result.skills ?? []).map((item) => toRecallTraceResult(item, "skill", previewChars, resourceTypes.skill)),
  ].slice(0, limit);
}

export function toTraceSelectedItem(
  item: FindResultItem,
  previewChars: number,
  extra: Partial<RecallTraceEntry["selected"][number]> = {},
): RecallTraceEntry["selected"][number] {
  return {
    uri: item.uri,
    resourceType: inferRecallResourceType(item.uri),
    category: item.category,
    score: item.score,
    abstractPreview: previewText(item.abstract ?? item.overview, previewChars),
    ...extra,
  };
}
