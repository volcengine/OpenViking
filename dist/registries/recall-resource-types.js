export const ALLOWED_RECALL_RESOURCE_TYPES = ["resource", "user", "agent"];
export const DEFAULT_RECALL_RESOURCE_TYPES = ["user", "agent"];
function toResourceTypeEntries(value) {
    if (Array.isArray(value)) {
        return value
            .filter((entry) => typeof entry === "string")
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
export function normalizeRecallResourceTypes(value) {
    const entries = toResourceTypeEntries(value);
    if (entries.length === 0) {
        return [...DEFAULT_RECALL_RESOURCE_TYPES];
    }
    const seen = new Set();
    const normalized = [];
    const invalid = [];
    for (const entry of entries) {
        if (ALLOWED_RECALL_RESOURCE_TYPES.includes(entry)) {
            const typed = entry;
            if (!seen.has(typed)) {
                seen.add(typed);
                normalized.push(typed);
            }
        }
        else {
            invalid.push(entry);
        }
    }
    if (invalid.length > 0) {
        throw new Error(`invalid resourceTypes: ${invalid.join(", ")}`);
    }
    return normalized.length > 0 ? normalized : [...DEFAULT_RECALL_RESOURCE_TYPES];
}
export function resolveRecallSearchPlan(resourceTypes, _ctx) {
    const normalized = normalizeRecallResourceTypes(resourceTypes);
    const searches = [];
    const skipped = [];
    let addedMemorySearch = false;
    for (const resourceType of normalized) {
        if (resourceType === "resource") {
            searches.push({ resourceType, contextType: "resource" });
        }
        else if ((resourceType === "user" || resourceType === "agent") && !addedMemorySearch) {
            searches.push({ resourceType: "user", contextType: "memory" });
            addedMemorySearch = true;
        }
    }
    return { resourceTypes: normalized, searches, skipped };
}
