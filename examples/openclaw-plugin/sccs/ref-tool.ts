import type { RefStore } from "./storage.js";
import { normalizeRefId } from "./utils.js";
export function createFetchOriginalDataTool(params: {
  store: RefStore;
  logger?: { info?: (msg: string) => void };
}) {
  return {
    name: "fetch_original_data",
    label: "Fetch Original Data (SCCS)",
    description: "Retrieve the full original output for one or more REF_ID placeholders.",
    parameters: {
      type: "object",
      properties: {
        ref_ids: {
          type: "array",
          items: { type: "string" },
          description: "List of REF_ID hashes or [REF_ID: ...] strings",
        },
      },
      required: ["ref_ids"],
    },
    async execute(_toolCallId: string, input: unknown) {
      const value = input as { ref_ids?: unknown };
      const refIds = Array.isArray(value?.ref_ids) ? value.ref_ids : [];
      const normalized = refIds
        .filter((v) => typeof v === "string")
        .map((v) => normalizeRefId(v))
        .filter((v): v is string => v !== null);
      if (normalized.length === 0) {
        return { content: [{ type: "text", text: "No valid REF_ID provided." }] };
      }

      const sections: string[] = [];
      for (const refId of normalized) {
        const content = await params.store.get(refId);
        sections.push(
          content ? `REF_ID ${refId}:\n${content}` : `REF_ID ${refId}: <not found or expired>`,
        );
      }

      params.logger?.info?.(`[sccs] fetch_original_data: ${normalized.length} ids`);
      return { content: [{ type: "text", text: sections.join("\n\n") }] };
    },
  };
}
