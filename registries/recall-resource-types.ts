export const ALLOWED_RECALL_RESOURCE_TYPES = ["resource", "user", "agent"] as const;
export type RecallResourceType = typeof ALLOWED_RECALL_RESOURCE_TYPES[number];
export const DEFAULT_RECALL_RESOURCE_TYPES: readonly RecallResourceType[] = ["user", "agent"];

export type RecallSearchPlan = {
  resourceTypes: RecallResourceType[];
  searches: Array<{ resourceType: RecallResourceType; contextType: "memory" | "resource"; targetUri?: string }>;
  skipped: Array<{ resourceType: RecallResourceType; reason: "missing_session" }>;
};

function toResourceTypeEntries(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value
      .filter((entry): entry is string => typeof entry === "string")
      .map((entry) => entry.trim())
      .filter(Boolean);
  }
  if (typeof value === "string") {
    return value
      .split(/[,\n]/)
      .map((entry) => entry.trim())
      .filter(Boolean);
  }
  return [];
}

export function normalizeRecallResourceTypes(value: unknown): RecallResourceType[] {
  const entries = toResourceTypeEntries(value);
  if (entries.length === 0) {
    return [...DEFAULT_RECALL_RESOURCE_TYPES];
  }

  const seen = new Set<RecallResourceType>();
  const normalized: RecallResourceType[] = [];
  const invalid: string[] = [];
  for (const entry of entries) {
    if ((ALLOWED_RECALL_RESOURCE_TYPES as readonly string[]).includes(entry)) {
      const typed = entry as RecallResourceType;
      if (!seen.has(typed)) {
        seen.add(typed);
        normalized.push(typed);
      }
    } else {
      invalid.push(entry);
    }
  }

  if (invalid.length > 0) {
    throw new Error(`invalid resourceTypes: ${invalid.join(", ")}`);
  }

  return normalized.length > 0 ? normalized : [...DEFAULT_RECALL_RESOURCE_TYPES];
}

export function resolveRecallSearchPlan(
  resourceTypes: unknown,
  _ctx: { ovSessionId?: string; agentId?: string },
): RecallSearchPlan {
  const normalized = normalizeRecallResourceTypes(resourceTypes);
  const searches: RecallSearchPlan["searches"] = [];
  const skipped: RecallSearchPlan["skipped"] = [];
  let addedMemorySearch = false;

  for (const resourceType of normalized) {
    if (resourceType === "resource") {
      searches.push({ resourceType, contextType: "resource" });
    } else if ((resourceType === "user" || resourceType === "agent") && !addedMemorySearch) {
      searches.push({ resourceType: "user", contextType: "memory" });
      addedMemorySearch = true;
    }
  }

  return { resourceTypes: normalized, searches, skipped };
}
