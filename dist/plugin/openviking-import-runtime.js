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
export function createOpenVikingImportRuntime(deps) {
    const importResource = async (input, agentId) => {
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
    const importSkill = async (input, agentId) => {
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
    const addResourceOpenViking = (input, agentId) => importResource({
        pathOrUrl: input.source ?? "",
        to: input.to,
        parent: input.parent,
        reason: input.reason,
        instruction: input.instruction,
        wait: input.wait,
        timeout: input.timeout,
    }, agentId);
    const addSkillOpenViking = (input, agentId) => importSkill({
        path: input.source,
        data: input.data,
        wait: input.wait,
        timeout: input.timeout,
    }, agentId);
    return { addResourceOpenViking, addSkillOpenViking };
}
