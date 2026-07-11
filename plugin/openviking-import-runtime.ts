import type {
  AddResourceInput,
  AddResourceResult,
  AddSkillInput,
  AddSkillResult,
} from "../client.js";

export type OpenVikingImportRuntimeResourceInput = {
  source?: string;
  to?: string;
  parent?: string;
  reason?: string;
  instruction?: string;
  wait?: boolean;
  timeout?: number;
};

export type OpenVikingImportRuntimeSkillInput = {
  source?: string;
  data?: unknown;
  wait?: boolean;
  timeout?: number;
};

export type OpenVikingImportRuntimeToolResult = {
  content: Array<{ type: "text"; text: string }>;
  details?: Record<string, unknown>;
};

export type OpenVikingImportRuntimeClient = {
  addResource: (input: AddResourceInput, agentId?: string) => Promise<AddResourceResult>;
  addSkill: (input: AddSkillInput, agentId?: string) => Promise<AddSkillResult>;
};

export type OpenVikingImportRuntimeDeps = {
  getClient: () => Promise<OpenVikingImportRuntimeClient>;
};

function formatResourceImportText(result: AddResourceResult): string {
  const root = result.root_uri ? ` ${result.root_uri}` : "";
  const warnings = result.warnings?.length ? ` Warnings: ${result.warnings.join("; ")}` : "";
  return `Imported OpenViking resource.${root}${warnings}`.trim();
}

function formatSkillImportText(result: AddSkillResult): string {
  const uri = result.uri ? ` ${result.uri}` : "";
  const name = result.name ? ` (${result.name})` : "";
  return `Imported OpenViking skill${name}.${uri}`.trim();
}

export function createOpenVikingImportRuntime(deps: OpenVikingImportRuntimeDeps): {
  addResourceOpenViking: (
    input: OpenVikingImportRuntimeResourceInput,
    agentId?: string,
  ) => Promise<OpenVikingImportRuntimeToolResult>;
  addSkillOpenViking: (
    input: OpenVikingImportRuntimeSkillInput,
    agentId?: string,
  ) => Promise<OpenVikingImportRuntimeToolResult>;
} {
  const importResource = async (input: AddResourceInput, agentId?: string): Promise<OpenVikingImportRuntimeToolResult> => {
    const client = await deps.getClient();
    const result = await client.addResource(input, agentId);
    return {
      content: [{ type: "text", text: formatResourceImportText(result) }],
      details: {
        action: "resource_imported",
        ...result,
      },
    };
  };

  const importSkill = async (input: AddSkillInput, agentId?: string): Promise<OpenVikingImportRuntimeToolResult> => {
    const client = await deps.getClient();
    const result = await client.addSkill(input, agentId);
    return {
      content: [{ type: "text", text: formatSkillImportText(result) }],
      details: {
        action: "skill_imported",
        ...result,
      },
    };
  };

  const addResourceOpenViking = (input: OpenVikingImportRuntimeResourceInput, agentId?: string) =>
    importResource({
      pathOrUrl: input.source ?? "",
      to: input.to,
      parent: input.parent,
      reason: input.reason,
      instruction: input.instruction,
      wait: input.wait,
      timeout: input.timeout,
    }, agentId);

  const addSkillOpenViking = (input: OpenVikingImportRuntimeSkillInput, agentId?: string) =>
    importSkill({
      path: input.source,
      data: input.data,
      wait: input.wait,
      timeout: input.timeout,
    }, agentId);

  return { addResourceOpenViking, addSkillOpenViking };
}
