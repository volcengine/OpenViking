import { AUTO_RECALL_SOURCE_MARKER, } from "./auto-recall.js";
import { compileSessionPatterns, shouldBypassSession, } from "./text-utils.js";
import { estimateAgentMessageTokens, estimateAgentMessagesTokens } from "./token-estimator.js";
import { openClawSessionToOvStorageId } from "./routing/identity-routing.js";
import { assembleOpenVikingSession, afterTurnOpenVikingSession, compactOpenVikingSession, commitOpenVikingSession, } from "./services/context-lifecycle-service.js";
function roughEstimate(messages) {
    return estimateAgentMessagesTokens(messages);
}
function msgTokenEstimate(msg) {
    return estimateAgentMessageTokens(msg);
}
function messageDigest(messages, maxCharsPerMsg = 2000) {
    return messages.map((msg) => {
        const m = msg;
        const role = String(m.role ?? "unknown");
        const raw = m.content;
        let text;
        if (typeof raw === "string") {
            text = raw;
        }
        else if (Array.isArray(raw)) {
            text = raw
                .map((b) => {
                if (b.type === "text")
                    return String(b.text ?? "");
                if (b.type === "toolCall")
                    return `[toolCall: ${String(b.name)}(${JSON.stringify(b.arguments ?? {}).slice(0, 200)})]`;
                if (b.type === "toolResult")
                    return `[toolResult: ${JSON.stringify(b.content ?? "").slice(0, 200)}]`;
                return `[${String(b.type)}]`;
            })
                .join("\n");
        }
        else {
            text = JSON.stringify(raw) ?? "";
        }
        const truncated = text.length > maxCharsPerMsg;
        return {
            role,
            content: truncated ? text.slice(0, maxCharsPerMsg) + "..." : text,
            tokens: msgTokenEstimate(msg),
            truncated,
        };
    });
}
function extractAgentMessageText(message) {
    if (!message) {
        return "";
    }
    const raw = message.content;
    if (typeof raw === "string") {
        return raw;
    }
    if (Array.isArray(raw)) {
        return raw
            .map((block) => {
            if (!block || typeof block !== "object") {
                return "";
            }
            const b = block;
            if (b.type === "text" && typeof b.text === "string") {
                return b.text;
            }
            return "";
        })
            .filter(Boolean)
            .join("\n");
    }
    return "";
}
function hasAutoRecallBlock(message) {
    return extractAgentMessageText(message).includes(AUTO_RECALL_SOURCE_MARKER);
}
function prependTextToMessageContent(content, text) {
    if (typeof content === "string") {
        return `${text}\n\n${content}`;
    }
    if (Array.isArray(content)) {
        if (content.length === 0) {
            return [{ type: "text", text }];
        }
        const first = content[0];
        if (first &&
            typeof first === "object" &&
            first.type === "text" &&
            typeof first.text === "string") {
            return [
                {
                    ...first,
                    text: `${text}\n\n${first.text}`,
                },
                ...content.slice(1),
            ];
        }
        return [{ type: "text", text }, ...content];
    }
    return text;
}
function prependRecallToLatestUserMessage(messages, recallBlock) {
    const latest = messages.at(-1);
    if (!latest || latest.role !== "user" || hasAutoRecallBlock(latest)) {
        return messages;
    }
    return [
        ...messages.slice(0, -1),
        {
            ...latest,
            content: prependTextToMessageContent(latest.content, recallBlock),
        },
    ];
}
function emitDiag(log, stage, sessionId, data, enabled = true) {
    if (!enabled)
        return;
    log.info(`openviking: diag ${JSON.stringify({ ts: Date.now(), stage, sessionId, data })}`);
}
function validTokenBudget(raw) {
    if (typeof raw === "number" && Number.isFinite(raw) && raw > 0) {
        return raw;
    }
    return undefined;
}
export function createMemoryOpenVikingContextEngine(params) {
    const { id, name, version, cfg, logger, getClient, resolveAgentId, rememberSessionAgentId, queryConfigStore, traceRecorder, } = params;
    const diagEnabled = cfg.emitStandardDiagnostics;
    const bypassSessionPatterns = compileSessionPatterns(cfg.bypassSessionPatterns);
    const diag = (stage, sessionId, data) => emitDiag(logger, stage, sessionId, data, diagEnabled);
    const isBypassedSession = (params) => shouldBypassSession(params, bypassSessionPatterns);
    async function doCommitOVSession(params) {
        const { sessionId } = params;
        const { sessionKey } = resolveSessionIdentity(params);
        return commitOpenVikingSession({
            sessionId,
            sessionKey,
            getClient,
            logger,
            rememberSessionAgentId,
            resolveAgentId,
            isBypassedSession,
        });
    }
    function extractSessionKey(runtimeContext) {
        if (!runtimeContext) {
            return undefined;
        }
        const key = runtimeContext.sessionKey;
        return typeof key === "string" && key.trim() ? key.trim() : undefined;
    }
    function resolveSessionKey(params) {
        const direct = typeof params.sessionKey === "string" ? params.sessionKey.trim() : "";
        if (direct) {
            return direct;
        }
        return extractSessionKey(params.runtimeContext);
    }
    function resolveSessionIdentity(params) {
        const sessionKey = resolveSessionKey(params);
        return {
            sessionKey,
            ovSessionId: openClawSessionToOvStorageId(params.sessionId, sessionKey),
        };
    }
    return {
        info: {
            id,
            name,
            version,
            ownsCompaction: true,
        },
        commitOVSession: doCommitOVSession,
        // --- standard ContextEngine methods ---
        async ingest() {
            return { ingested: false };
        },
        async ingestBatch() {
            return { ingestedCount: 0 };
        },
        async assemble(assembleParams) {
            const tokenBudget = validTokenBudget(assembleParams.tokenBudget) ?? 128_000;
            const isMainAssemble = Object.prototype.hasOwnProperty.call(assembleParams, "availableTools") ||
                Object.prototype.hasOwnProperty.call(assembleParams, "citationsMode") ||
                Object.prototype.hasOwnProperty.call(assembleParams, "prompt");
            return assembleOpenVikingSession({
                sessionId: assembleParams.sessionId,
                sessionKey: resolveSessionKey(assembleParams),
                messages: assembleParams.messages,
                tokenBudget,
                runtimeContext: assembleParams.runtimeContext,
                isMainAssemble,
                cfg,
                getClient,
                logger,
                resolveAgentId,
                rememberSessionAgentId,
                isBypassedSession,
                queryConfigStore,
                traceRecorder,
                diag,
                roughEstimate,
                messageDigest,
                extractAgentMessageText,
                hasAutoRecallBlock,
                prependRecallToLatestUserMessage,
            });
        },
        async afterTurn(afterTurnParams) {
            const tokenBudget = validTokenBudget(afterTurnParams.tokenBudget) ?? 128_000;
            await afterTurnOpenVikingSession({
                sessionId: afterTurnParams.sessionId,
                sessionKey: resolveSessionKey(afterTurnParams),
                messages: afterTurnParams.messages,
                prePromptMessageCount: afterTurnParams.prePromptMessageCount,
                isHeartbeat: afterTurnParams.isHeartbeat,
                tokenBudget,
                runtimeContext: afterTurnParams.runtimeContext,
                cfg,
                getClient,
                logger,
                resolveAgentId,
                rememberSessionAgentId,
                isBypassedSession,
                diag,
            });
        },
        async compact(compactParams) {
            const tokenBudget = validTokenBudget(compactParams.tokenBudget) ?? 128_000;
            return compactOpenVikingSession({
                sessionId: compactParams.sessionId,
                sessionKey: resolveSessionKey(compactParams),
                tokenBudget,
                currentTokenCount: compactParams.currentTokenCount,
                force: compactParams.force,
                compactionTarget: compactParams.compactionTarget,
                customInstructions: compactParams.customInstructions,
                getClient,
                logger,
                resolveAgentId,
                isBypassedSession,
                diag,
            });
        },
    };
}
