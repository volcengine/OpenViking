function tokenizeCommandArgs(args) {
    const tokens = [];
    let current = "";
    let quote = null;
    let escaping = false;
    for (let i = 0; i < args.length; i += 1) {
        const ch = args[i];
        const next = args[i + 1];
        if (escaping) {
            current += ch;
            escaping = false;
            continue;
        }
        if (ch === "\\") {
            const shouldEscape = quote === '"'
                ? next === '"' || next === "\\"
                : !quote && Boolean(next && (/\s/.test(next) || next === '"' || next === "'"));
            if (shouldEscape) {
                escaping = true;
                continue;
            }
            current += ch;
            continue;
        }
        if ((ch === '"' || ch === "'") && (!quote || quote === ch)) {
            quote = quote ? null : ch;
            continue;
        }
        if (!quote && /\s/.test(ch)) {
            if (current) {
                tokens.push(current);
                current = "";
            }
            continue;
        }
        current += ch;
    }
    if (escaping) {
        current += "\\";
    }
    if (quote) {
        throw new Error("Unterminated quoted argument");
    }
    if (current) {
        tokens.push(current);
    }
    return tokens;
}
function parseFlagArgs(args) {
    const tokens = tokenizeCommandArgs(args);
    const positionals = [];
    const flags = new Map();
    for (let i = 0; i < tokens.length; i += 1) {
        const token = tokens[i];
        if (!token.startsWith("--")) {
            positionals.push(token);
            continue;
        }
        const raw = token.slice(2);
        if (!raw) {
            continue;
        }
        const eqIndex = raw.indexOf("=");
        if (eqIndex >= 0) {
            flags.set(raw.slice(0, eqIndex), raw.slice(eqIndex + 1));
            continue;
        }
        const next = tokens[i + 1];
        if (next && !next.startsWith("--")) {
            flags.set(raw, next);
            i += 1;
        }
        else {
            flags.set(raw, true);
        }
    }
    return { positionals, flags };
}
function getStringFlag(flags, name) {
    const value = flags.get(name);
    return typeof value === "string" && value.trim() ? value.trim() : undefined;
}
function getNumberFlag(flags, name) {
    const raw = getStringFlag(flags, name);
    if (!raw) {
        return undefined;
    }
    const value = Number(raw);
    if (!Number.isFinite(value)) {
        throw new Error(`--${name} must be a number`);
    }
    return value;
}
function getOptionalBoolFlag(flags, name) {
    const value = flags.get(name);
    if (value === undefined)
        return undefined;
    if (value === true)
        return true;
    if (value === false)
        return false;
    const normalized = value.trim().toLowerCase();
    if (["1", "true", "yes", "on"].includes(normalized))
        return true;
    if (["0", "false", "no", "off"].includes(normalized))
        return false;
    throw new Error(`--${name} must be a boolean`);
}
function parseNumberMapFlag(flags, name) {
    const raw = getStringFlag(flags, name);
    if (!raw)
        return undefined;
    const result = {};
    for (const item of raw.split(",")) {
        const [key, rawValue, ...rest] = item.split("=");
        const trimmedKey = key?.trim();
        const trimmedValue = rawValue?.trim();
        if (!trimmedKey || !trimmedValue || rest.length > 0) {
            throw new Error(`--${name} must use key=value pairs separated by commas`);
        }
        const value = Number(trimmedValue);
        if (!Number.isFinite(value)) {
            throw new Error(`--${name} value for ${trimmedKey} must be a number`);
        }
        result[trimmedKey] = value;
    }
    return result;
}
function parseQueryConfigPatch(flags) {
    const patch = {};
    const numberFields = [
        ["recallLimit", "recallLimit"],
        ["candidateLimit", "candidateLimit"],
        ["candidateMultiplier", "candidateMultiplier"],
        ["scoreThreshold", "scoreThreshold"],
        ["maxInjectedChars", "maxInjectedChars"],
        ["ovSearchLimit", "ovSearchLimit"],
    ];
    for (const [flag, field] of numberFields) {
        if (flags.has(flag)) {
            patch[field] = getNumberFlag(flags, flag);
        }
    }
    const resourceTypes = getStringFlag(flags, "resourceTypes");
    if (resourceTypes)
        patch.resourceTypes = resourceTypes;
    const targetUri = getStringFlag(flags, "targetUri");
    if (targetUri)
        patch.targetUri = targetUri;
    const recallPreferAbstract = getOptionalBoolFlag(flags, "recallPreferAbstract");
    if (recallPreferAbstract !== undefined)
        patch.recallPreferAbstract = recallPreferAbstract;
    const rankingWeights = parseNumberMapFlag(flags, "weight") ?? parseNumberMapFlag(flags, "rankingWeights");
    if (rankingWeights)
        patch.rankingWeights = rankingWeights;
    const categoryWeights = parseNumberMapFlag(flags, "categoryWeight") ?? parseNumberMapFlag(flags, "categoryWeights");
    if (categoryWeights)
        patch.categoryWeights = categoryWeights;
    const resourceTypeWeights = parseNumberMapFlag(flags, "resourceTypeWeight") ?? parseNumberMapFlag(flags, "resourceTypeWeights");
    if (resourceTypeWeights)
        patch.resourceTypeWeights = resourceTypeWeights;
    return patch;
}
export function createOpenVikingQueryConfigCommandHandler(deps) {
    return async (ctx) => {
        const parsed = parseFlagArgs(ctx.args ?? "");
        const action = parsed.positionals[0] ?? "get";
        const session = deps.resolvePluginSessionRouting(ctx);
        const scope = getStringFlag(parsed.flags, "scope") === "claw" ? "claw" : "session";
        const queryCtx = deps.toQueryConfigContext(session);
        if (action === "get") {
            const effective = await deps.queryConfigStore.getEffective(queryCtx);
            return { text: JSON.stringify({ scope, effective }, null, 2), details: { scope, effective } };
        }
        if (action === "set") {
            const patch = parseQueryConfigPatch(parsed.flags);
            const { params, warnings } = deps.normalizeRuntimeQueryParams(patch);
            if (Object.keys(params).length === 0) {
                throw new Error("No query config parameters provided for /ov-query-config set");
            }
            await deps.queryConfigStore.set(scope, queryCtx, params);
            const effective = await deps.queryConfigStore.getEffective(queryCtx);
            return {
                text: `Updated OpenViking query config (${scope}).${warnings.length ? ` Warnings: ${warnings.join("; ")}` : ""}`,
                details: { scope, params, warnings, effective },
            };
        }
        if (action === "unset") {
            const fields = parsed.positionals.slice(1);
            if (fields.length === 0)
                throw new Error("Usage: /ov-query-config unset <field...> [--scope session|claw]");
            await deps.queryConfigStore.unset(scope, queryCtx, fields);
            const effective = await deps.queryConfigStore.getEffective(queryCtx);
            return { text: `Unset OpenViking query config fields (${scope}): ${fields.join(", ")}`, details: { scope, fields, effective } };
        }
        if (action === "reset") {
            await deps.queryConfigStore.reset(scope, queryCtx);
            const effective = await deps.queryConfigStore.getEffective(queryCtx);
            return { text: `Reset OpenViking query config (${scope}).`, details: { scope, effective } };
        }
        throw new Error("Usage: /ov-query-config get|set|unset|reset [--scope session|claw] [--recallLimit N] [--candidateLimit N] [--scoreThreshold N] [--resourceTypes user,agent]");
    };
}
