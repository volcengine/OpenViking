import type {
  VikingBotDraft,
  VikingBotMessage,
} from '../../components/viking-bot/model.js';
import { createChatAccumulator } from './aggregate.js';
import { draftParts } from './draft-parts.js';
import type { VikingBotChatEvent, VikingBotChatState } from './types.js';

export type AppendInput = (
  draft: VikingBotDraft,
  accepted: Promise<boolean>,
) => Promise<boolean>;
const id = () => globalThis.crypto.randomUUID();
const userMessage = (draft: VikingBotDraft): VikingBotMessage => ({
  id: id(),
  turnId: id(),
  role: 'user',
  createdAt: new Date().toISOString(),
  parts: draftParts(draft),
});

/** Accepted input appears after the active segment; the next iteration consumes it. */
export function createChatTimeline(
  base: readonly VikingBotMessage[],
  draft: VikingBotDraft,
  current: () => boolean,
  update: (patch: Partial<VikingBotChatState>) => void,
) {
  const first = userMessage(draft);
  const messages = [...base, first];
  let pending: VikingBotMessage[] = [];
  let turnId = first.turnId!;
  let accumulator = createChatAccumulator(turnId);
  let decisions = Promise.resolve();
  const publish = () =>
    update({
      messages: [...messages, ...pending],
      streaming: { ...accumulator.snapshot(), insertIndex: messages.length },
    });
  const commit = () => {
    const parts = accumulator.finish();
    if (parts.length)
      messages.push({
        id: id(),
        turnId,
        role: 'assistant',
        createdAt: new Date().toISOString(),
        parts,
      });
    messages.push(...pending);
    turnId = pending.at(-1)?.turnId ?? turnId;
    pending = [];
    accumulator = createChatAccumulator(turnId);
  };
  return {
    publish,
    append: (input: VikingBotDraft, accepted: Promise<boolean>) => {
      const message = userMessage(input);
      const applied = decisions.then(async () => {
        if (!(await accepted) || !current()) return false;
        pending.push(message);
        publish();
        return true;
      });
      decisions = applied.then(() => undefined);
      return applied;
    },
    apply: async (event: VikingBotChatEvent) => {
      await decisions;
      if (!current()) return;
      if (event.type === 'iteration' && pending.length) commit();
      accumulator.apply(event);
      publish();
    },
    finish: async () => {
      await decisions;
      commit();
      return { messages: [...messages], added: messages.slice(base.length) };
    },
  };
}
