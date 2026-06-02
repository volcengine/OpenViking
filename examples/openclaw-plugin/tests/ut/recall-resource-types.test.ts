import { describe, expect, it } from "vitest";

import {
  ALLOWED_RECALL_RESOURCE_TYPES,
  DEFAULT_RECALL_RESOURCE_TYPES,
  normalizeRecallResourceTypes,
  resolveRecallSearchPlan,
} from "../../registries/recall-resource-types.js";

describe("recall resource type registry", () => {
  it("defines the single allowed and default resource type source", () => {
    expect(ALLOWED_RECALL_RESOURCE_TYPES).toEqual(["resource", "session", "user", "agent"]);
    expect(DEFAULT_RECALL_RESOURCE_TYPES).toEqual(["user", "agent"]);
  });

  it("normalizes arrays and comma/newline-separated strings without changing legacy behavior", () => {
    expect(normalizeRecallResourceTypes(undefined)).toEqual(["user", "agent"]);
    expect(normalizeRecallResourceTypes([])).toEqual(["user", "agent"]);
    expect(normalizeRecallResourceTypes(" resource, session\nuser,agent,user ")).toEqual([
      "resource",
      "session",
      "user",
      "agent",
    ]);
    expect(() => normalizeRecallResourceTypes(["user", "project"])).toThrow("invalid resourceTypes: project");
  });

  it("builds target URIs and records skipped session searches", () => {
    expect(resolveRecallSearchPlan(["resource", "session", "user", "agent"], { ovSessionId: "ov-1" })).toEqual({
      resourceTypes: ["resource", "session", "user", "agent"],
      searches: [
        { resourceType: "resource", targetUri: "viking://resources" },
        { resourceType: "session", targetUri: "viking://session/ov-1/history" },
        { resourceType: "user", targetUri: "viking://user/memories" },
        { resourceType: "agent", targetUri: "viking://agent/memories" },
      ],
      skipped: [],
    });

    expect(resolveRecallSearchPlan(["session", "user"], {})).toEqual({
      resourceTypes: ["session", "user"],
      searches: [{ resourceType: "user", targetUri: "viking://user/memories" }],
      skipped: [{ resourceType: "session", reason: "missing_session" }],
    });
  });
});
