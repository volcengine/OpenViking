import { OvClientError } from '../../../client/errors.js';
import type { VikingBotDraft } from '../../../components/viking-bot/model.js';
import type { AppendInput } from './chat-timeline.js';
import type { VikingBotChatService, VikingBotChatState } from './types.js';

export async function steerInput({
  service,
  sessionId,
  draft,
  append,
  signal,
  current,
  update,
  submitting,
}: {
  service: VikingBotChatService;
  sessionId?: string;
  draft: VikingBotDraft;
  append?: AppendInput;
  signal: AbortSignal;
  current: () => boolean;
  update: (patch: Partial<VikingBotChatState>) => void;
  submitting: boolean;
}) {
  if (!service.steer || !sessionId || !append || submitting) return false;
  let decide!: (accepted: boolean) => void;
  const decision = new Promise<boolean>((resolve) => {
    decide = resolve;
  });
  const applied = append(draft, decision);
  update({ isSubmitting: true, error: undefined });
  try {
    const result = await service.steer({ sessionId, ...draft }, signal);
    signal.throwIfAborted();
    decide(result.accepted);
    if (!result.accepted)
      throw new OvClientError({
        code: 'BOT_INPUT_REJECTED',
        message: 'VikingBot did not accept the follow-up input',
      });
    return await applied;
  } catch (error) {
    decide(false);
    await applied;
    if (current() && !signal.aborted)
      update({
        error: error instanceof Error ? error : new Error(String(error)),
      });
    return false;
  } finally {
    if (current()) update({ isSubmitting: false });
  }
}
