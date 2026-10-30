import type { PromptHookRecallParams } from "../services/context-lifecycle-service.js";

export type PromptRecallHookContext = {
  agentId?: string;
  sessionId?: string;
  sessionKey?: string;
  senderId?: string;
};

type PromptRecallHookEvent = {
  prompt?: unknown;
  currentUserMessage?: unknown;
  currentUserMessageId?: unknown;
};

export type OpenVikingPromptRecallHookApi = {
  on: (
    hookName: string,
    handler: (event: unknown, ctx?: PromptRecallHookContext) => unknown,
    opts?: { priority?: number },
  ) => void;
};

export type OpenVikingPromptRecallHookDeps = Omit<PromptHookRecallParams, "sessionId" | "sessionKey" | "runtimeContext" | "prompt"> & {
  api: OpenVikingPromptRecallHookApi;
  isBypassedSession: (ctx?: { sessionId?: string; sessionKey?: string }) => boolean;
  recall: (params: PromptHookRecallParams) => Promise<string>;
};

const RECALLED_TURNS_MAX = 64;

/**
 * OpenClaw registers before_prompt_build for a non-bundled plugin only with an
 * explicit conversation-access grant. Without it, recall stays on assemble.
 */
export function promptRecallHookGranted(hostConfig: unknown, pluginId: string): boolean {
  const entries = (hostConfig as { plugins?: { entries?: Record<string, unknown> } } | undefined)?.plugins?.entries;
  const hooks = (entries?.[pluginId] as { hooks?: Record<string, unknown> } | undefined)?.hooks;
  return hooks?.allowConversationAccess === true && hooks.allowPromptInjection !== false;
}

/**
 * Recall once per admitted user turn and return it as prependContext. OpenClaw
 * freezes that text into the turn and replays it unchanged, so the history
 * prefix stays cacheable, unlike a per-turn systemPromptAddition.
 */
export function registerOpenVikingPromptRecallHook(deps: OpenVikingPromptRecallHookDeps): void {
  const { api, cfg, logger, isBypassedSession, recall, ...recallDeps } = deps;
  // Retries and model fallback rebuild the prompt for the same admission; reuse
  // the first result instead of searching again into the server cooldown.
  const recalledTurns = new Map<string, string>();

  api.on("before_prompt_build", async (event: unknown, ctx?: PromptRecallHookContext) => {
    if (!cfg.autoRecall) return undefined;
    const sessionId = ctx?.sessionId ?? ctx?.sessionKey;
    if (!sessionId || isBypassedSession(ctx)) return undefined;

    const { prompt, currentUserMessage, currentUserMessageId } = (event ?? {}) as PromptRecallHookEvent;
    // An explicit empty currentUserMessage means no textual request; `prompt` may hold rebuilt history.
    const text = typeof currentUserMessage === "string" ? currentUserMessage : typeof prompt === "string" ? prompt : "";
    const turnKey = typeof currentUserMessageId === "string" && currentUserMessageId
      ? `${sessionId}\u0000${currentUserMessageId}`
      : undefined;

    let block = turnKey ? recalledTurns.get(turnKey) : undefined;
    if (block === undefined) {
      try {
        block = await recall({
          ...recallDeps,
          cfg,
          logger,
          sessionId,
          sessionKey: ctx?.sessionKey,
          runtimeContext: ctx?.senderId ? { senderId: ctx.senderId } : undefined,
          prompt: text,
        });
      } catch (err) {
        logger.warn?.(`openviking: auto-recall failed: ${String(err)}`);
        return undefined;
      }
      if (turnKey) {
        recalledTurns.set(turnKey, block);
        if (recalledTurns.size > RECALLED_TURNS_MAX) {
          recalledTurns.delete(recalledTurns.keys().next().value as string);
        }
      }
    }
    return block ? { prependContext: block } : undefined;
  });
}
