export const ALLOWED_RECALL_RESOURCE_TYPES = ["resource", "session", "user", "agent"] as const;
export type RecallResourceType = typeof ALLOWED_RECALL_RESOURCE_TYPES[number];
export const DEFAULT_RECALL_RESOURCE_TYPES: readonly RecallResourceType[] = ["user", "agent"];

export type RecallSearchPlan = {
  resourceTypes: RecallResourceType[];
  searches: Array<{ resourceType: RecallResourceType; targetUri: string }>;
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
  ctx: { ovSessionId?: string; agentId?: string },
): RecallSearchPlan {
  const normalized = normalizeRecallResourceTypes(resourceTypes);
  const searches: RecallSearchPlan["searches"] = [];
  const skipped: RecallSearchPlan["skipped"] = [];

  for (const resourceType of normalized) {
    if (resourceType === "resource") {
      searches.push({ resourceType, targetUri: "viking://resources" });
    } else if (resourceType === "session") {
      if (ctx.ovSessionId) {
        searches.push({ resourceType, targetUri: `viking://session/${ctx.ovSessionId}/history` });
      } else {
        skipped.push({ resourceType, reason: "missing_session" });
      }
    } else if (resourceType === "user") {
      searches.push({ resourceType, targetUri: "viking://user/memories" });
    } else if (resourceType === "agent") {
      searches.push({ resourceType, targetUri: "viking://agent/memories" });
    }
  }

  return { resourceTypes: normalized, searches, skipped };
}
