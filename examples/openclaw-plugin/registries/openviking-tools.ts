export type OpenVikingToolGroup =
  | "memory"
  | "resource_query"
  | "import"
  | "recall_trace"
  | "archive"
  | "tool_result";

export type OpenVikingToolName =
  | "add_resource"
  | "add_skill"
  | "ov_search"
  | "ov_read"
  | "ov_multi_read"
  | "ov_list"
  | "memory_recall"
  | "ov_recall_trace"
  | "memory_store"
  | "memory_forget"
  | "ov_archive_search"
  | "ov_archive_expand"
  | "openviking_tool_result_read"
  | "openviking_tool_result_search"
  | "openviking_tool_result_list";

export type OpenVikingToolSpec = {
  name: OpenVikingToolName;
  group: OpenVikingToolGroup;
  defaultEnabled: boolean;
  requiresLegacyFlag?: "enableAddResourceTool";
};

export const OPENVIKING_ADD_RESOURCE_TOOL_NAME = "add_resource" as const;

export const OPENVIKING_TOOL_SPECS = [
  {
    name: OPENVIKING_ADD_RESOURCE_TOOL_NAME,
    group: "import",
    defaultEnabled: false,
    requiresLegacyFlag: "enableAddResourceTool",
  },
  { name: "add_skill", group: "import", defaultEnabled: true },
  { name: "ov_search", group: "resource_query", defaultEnabled: true },
  { name: "ov_read", group: "resource_query", defaultEnabled: true },
  { name: "ov_multi_read", group: "resource_query", defaultEnabled: true },
  { name: "ov_list", group: "resource_query", defaultEnabled: true },
  { name: "memory_recall", group: "memory", defaultEnabled: true },
  { name: "ov_recall_trace", group: "recall_trace", defaultEnabled: true },
  { name: "memory_store", group: "memory", defaultEnabled: true },
  { name: "memory_forget", group: "memory", defaultEnabled: true },
  { name: "ov_archive_search", group: "archive", defaultEnabled: true },
  { name: "ov_archive_expand", group: "archive", defaultEnabled: true },
  { name: "openviking_tool_result_read", group: "tool_result", defaultEnabled: true },
  { name: "openviking_tool_result_search", group: "tool_result", defaultEnabled: true },
  { name: "openviking_tool_result_list", group: "tool_result", defaultEnabled: true },
] as const satisfies readonly OpenVikingToolSpec[];

export const OPENVIKING_ALL_TOOL_NAMES = OPENVIKING_TOOL_SPECS.map((spec) => spec.name) as OpenVikingToolName[];

export const OPENVIKING_DEFAULT_ENABLED_TOOL_NAMES = OPENVIKING_TOOL_SPECS
  .filter((spec) => spec.defaultEnabled)
  .map((spec) => spec.name) as OpenVikingToolName[];

const OPENVIKING_TOOL_GROUP_ORDER: readonly OpenVikingToolGroup[] = [
  "memory",
  "resource_query",
  "import",
  "recall_trace",
  "archive",
  "tool_result",
];

export const OPENVIKING_TOOL_GROUPS: Record<string, readonly OpenVikingToolName[]> = {
  all: OPENVIKING_ALL_TOOL_NAMES,
  default: OPENVIKING_DEFAULT_ENABLED_TOOL_NAMES,
  ...Object.fromEntries(
    OPENVIKING_TOOL_GROUP_ORDER.map((group) => [
      group,
      OPENVIKING_TOOL_SPECS
        .filter((spec) => spec.group === group)
        .map((spec) => spec.name),
    ]),
  ),
};
