import { describe, expect, it } from "vitest";

import { memoryOpenVikingConfigSchema } from "../../config.js";
import {
  OPENVIKING_ADD_RESOURCE_TOOL_NAME,
  OPENVIKING_ALL_TOOL_NAMES,
  OPENVIKING_DEFAULT_ENABLED_TOOL_NAMES,
  OPENVIKING_TOOL_GROUPS,
  OPENVIKING_TOOL_SPECS,
} from "../../registries/openviking-tools.js";

const DEFAULT_TOOL_NAMES = [
  "add_skill",
  "ov_search",
  "ov_read",
  "ov_multi_read",
  "ov_list",
  "memory_recall",
  "ov_recall_trace",
  "memory_store",
  "memory_forget",
  "ov_archive_search",
  "ov_archive_expand",
  "openviking_tool_result_read",
  "openviking_tool_result_search",
  "openviking_tool_result_list",
] as const;

describe("openviking tool registry", () => {
  it("defines every tool once in registration order", () => {
    expect(OPENVIKING_ADD_RESOURCE_TOOL_NAME).toBe("add_resource");
    expect(OPENVIKING_ALL_TOOL_NAMES).toEqual(["add_resource", ...DEFAULT_TOOL_NAMES]);
    expect(OPENVIKING_TOOL_SPECS.map((spec) => spec.name)).toEqual(OPENVIKING_ALL_TOOL_NAMES);
    expect(new Set(OPENVIKING_TOOL_SPECS.map((spec) => spec.name)).size).toBe(OPENVIKING_TOOL_SPECS.length);
  });

  it("derives default enabled tools and groups from ToolSpec", () => {
    expect(OPENVIKING_DEFAULT_ENABLED_TOOL_NAMES).toEqual(DEFAULT_TOOL_NAMES);
    expect(OPENVIKING_TOOL_SPECS.filter((spec) => spec.defaultEnabled).map((spec) => spec.name)).toEqual(
      OPENVIKING_DEFAULT_ENABLED_TOOL_NAMES,
    );
    expect(OPENVIKING_TOOL_GROUPS).toEqual({
      all: ["add_resource", ...DEFAULT_TOOL_NAMES],
      default: DEFAULT_TOOL_NAMES,
      memory: ["memory_recall", "memory_store", "memory_forget"],
      resource_query: ["ov_search", "ov_read", "ov_multi_read", "ov_list"],
      import: ["add_resource", "add_skill"],
      recall_trace: ["ov_recall_trace"],
      archive: ["ov_archive_search", "ov_archive_expand"],
      tool_result: [
        "openviking_tool_result_read",
        "openviking_tool_result_search",
        "openviking_tool_result_list",
      ],
    });
  });

  it("keeps add_resource behind the legacy enableAddResourceTool flag", () => {
    expect(OPENVIKING_TOOL_SPECS.find((spec) => spec.name === "add_resource")).toMatchObject({
      defaultEnabled: false,
      requiresLegacyFlag: "enableAddResourceTool",
    });

    expect(memoryOpenVikingConfigSchema.parse({}).enabledTools).not.toContain("add_resource");
    expect(memoryOpenVikingConfigSchema.parse({ enableAddResourceTool: true }).enabledTools).toContain("add_resource");
  });
});
