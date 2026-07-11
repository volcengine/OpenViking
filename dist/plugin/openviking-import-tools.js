import { Type } from "@sinclair/typebox";
function formatResourceImportText(result) {
    const root = result.root_uri ? ` ${result.root_uri}` : "";
    const warnings = result.warnings?.length ? ` Warnings: ${result.warnings.join("; ")}` : "";
    return `Imported OpenViking resource.${root}${warnings}`.trim();
}
function formatSkillImportText(result) {
    const uri = result.uri ? ` ${result.uri}` : "";
    const name = result.name ? ` (${result.name})` : "";
    return `Imported OpenViking skill${name}.${uri}`.trim();
}
export function registerOpenVikingImportTools(deps) {
    if (deps.enableAddResourceTool) {
        deps.registerTool((ctx) => ({
            name: "add_resource",
            label: "Add Resource (OpenViking)",
            description: "Use only when the user explicitly asks to import, add, upload, save, or index a document, directory, URL, Git repository, or OpenClaw media attachment into OpenViking resources. " +
                "Never use this during search, retrieval, URI reading, or search-result optimization; use ov_search and ov_read for those flows. " +
                "For a '[media attached: /path ...]' document, set source to that exact local media path. Do not invent OpenViking upload REST endpoints.",
            parameters: Type.Object({
                source: Type.String({ description: "Local path, OpenClaw media attachment path, directory path, public URL, or Git URL" }),
                to: Type.Optional(Type.String({ description: "Exact target URI, e.g. viking://resources/project-docs" })),
                parent: Type.Optional(Type.String({ description: "Parent URI under viking://resources" })),
                reason: Type.Optional(Type.String({ description: "Reason or note for adding this resource" })),
                instruction: Type.Optional(Type.String({ description: "Processing instruction for semantic extraction" })),
                wait: Type.Optional(Type.Boolean({ description: "Wait for processing to complete" })),
                timeout: Type.Optional(Type.Number({ description: "Timeout in seconds when wait is true" })),
            }),
            async execute(_toolCallId, params) {
                if (deps.isBypassedSession(ctx)) {
                    return deps.makeBypassedToolResult("add_resource");
                }
                const session = deps.resolvePluginSessionRouting(ctx);
                const client = await deps.getClient();
                const result = await client.addResource({
                    pathOrUrl: typeof params.source === "string" ? params.source : "",
                    to: typeof params.to === "string" ? params.to : undefined,
                    parent: typeof params.parent === "string" ? params.parent : undefined,
                    reason: typeof params.reason === "string" ? params.reason : undefined,
                    instruction: typeof params.instruction === "string" ? params.instruction : undefined,
                    wait: typeof params.wait === "boolean" ? params.wait : undefined,
                    timeout: typeof params.timeout === "number" ? params.timeout : undefined,
                }, session.agentId);
                return {
                    content: [{ type: "text", text: formatResourceImportText(result) }],
                    details: {
                        action: "resource_imported",
                        ...result,
                    },
                };
            },
        }), { name: "add_resource" });
    }
    deps.registerTool((ctx) => ({
        name: "add_skill",
        label: "Add Skill (OpenViking)",
        description: "Use only when the user explicitly asks to import, add, install, or register a skill into OpenViking. " +
            "Set source to a local SKILL.md file or skill directory, or data to raw SKILL.md content or an MCP tool dict.",
        parameters: Type.Object({
            source: Type.Optional(Type.String({ description: "Local SKILL.md path or skill directory path" })),
            data: Type.Optional(Type.Any({ description: "Raw SKILL.md content or MCP tool dict" })),
            wait: Type.Optional(Type.Boolean({ description: "Wait for processing to complete" })),
            timeout: Type.Optional(Type.Number({ description: "Timeout in seconds when wait is true" })),
        }),
        async execute(_toolCallId, params) {
            if (deps.isBypassedSession(ctx)) {
                return deps.makeBypassedToolResult("add_skill");
            }
            const session = deps.resolvePluginSessionRouting(ctx);
            const client = await deps.getClient();
            const result = await client.addSkill({
                path: typeof params.source === "string" ? params.source : undefined,
                data: params.data,
                wait: typeof params.wait === "boolean" ? params.wait : undefined,
                timeout: typeof params.timeout === "number" ? params.timeout : undefined,
            }, session.agentId);
            return {
                content: [{ type: "text", text: formatSkillImportText(result) }],
                details: {
                    action: "skill_imported",
                    ...result,
                },
            };
        },
    }), { name: "add_skill" });
}
