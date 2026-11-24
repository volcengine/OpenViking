import type { VikingBotChatState } from './types.js';

/** Remember interruptions only for the current controller and stable turn IDs. */
export function createInterruptedReplyHistory() {
  const turnsBySession = new Map<string, Set<string>>();
  return (state: VikingBotChatState, patch: Partial<VikingBotChatState>) => {
    const next = { ...state, ...patch };
    const { sessionId } = next;
    if (!sessionId || patch.status !== 'idle' || !patch.messages) return next;
    const turns = turnsBySession.get(sessionId) ?? new Set<string>();
    if (state.status === 'streaming') {
      for (const message of patch.messages) {
        if (
          message.role === 'assistant' &&
          message.interrupted &&
          message.turnId
        )
          turns.add(message.turnId);
      }
      if (turns.size) turnsBySession.set(sessionId, turns);
    }
    if (state.status === 'loading' && turns.size) {
      next.messages = patch.messages.map((message) =>
        message.role === 'assistant' &&
        message.interrupted === undefined &&
        message.turnId &&
        turns.has(message.turnId)
          ? { ...message, interrupted: true }
          : message,
      );
    }
    return next;
  };
}
