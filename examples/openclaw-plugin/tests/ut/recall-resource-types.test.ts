import { describe, expect, it } from "vitest";

import {
  ALLOWED_RECALL_RESOURCE_TYPES,
  DEFAULT_RECALL_RESOURCE_TYPES,
  normalizeRecallResourceTypes,
  resolveRecallSearchPlan,
} from "../../registries/recall-resource-types.js";

describe("recall resource type registry", () => {
  it("defines the single allowed and default resource type source", () => {
    expect(ALLOWED_RECALL_RESOURCE_TYPES).toEqual(["resource", "user", "agent"]);
    expect(DEFAULT_RECALL_RESOURCE_TYPES).toEqual(["user", "agent"]);
  });

  it("normalizes arrays and comma/newline-separated strings without changing legacy behavior", () => {
    expect(normalizeRecallResourceTypes(undefined)).toEqual(["user", "agent"]);
    expect(normalizeRecallResourceTypes([])).toEqual(["user", "agent"]);
    expect(normalizeRecallResourceTypes(" resource,\nuser,agent,user ")).toEqual([
      "resource",
      "user",
      "agent",
    ]);
    expect(() => normalizeRecallResourceTypes(["user", "project"])).toThrow("invalid resourceTypes: project");
  });

  it("builds context-type search plans without deprecated agent/session URI paths", () => {
    expect(resolveRecallSearchPlan(["resource", "user", "agent"], { ovSessionId: "ov-1" })).toEqual({
      resourceTypes: ["resource", "user", "agent"],
      searches: [
        { resourceType: "resource", contextType: "resource" },
        { resourceType: "user", contextType: "memory" },
      ],
      skipped: [],
    });

    expect(resolveRecallSearchPlan(["agent", "user"], {})).toEqual({
      resourceTypes: ["agent", "user"],
      searches: [{ resourceType: "user", contextType: "memory" }],
      skipped: [],
    });
  });
});
