import type { VikingBotInteractionAnswer } from '../../../components/viking-bot/index.js';
import type { VikingBotMessage } from '../../../components/viking-bot/model.js';
import { formatAnswers } from './answers.js';
import type { HitlGroup } from './types.js';
import type { VikingBotChatResult } from '../chat/types.js';

/** Report one native answer payload through the existing conversation transport. */
async function deliverHitlAnswer(
  send: VikingBotChatResult['send'],
  input: string,
  close: () => void,
  fail: () => void,
) {
  let delivered = false;
  const sent = await send({ text: input, attachments: [] }, () => {
    delivered = true;
    close();
  });
  if (delivered) return;
  if (sent) close();
  else fail();
}

export interface HitlDraft {
  owner: object;
  group: HitlGroup;
  turnUser?: VikingBotMessage;
  answers: Readonly<Record<string, VikingBotInteractionAnswer>>;
  closed?: boolean;
  manual?: boolean;
  failedInput?: string;
  pendingInput?: string;
}

export function createHitlDelivery(
  draft: HitlDraft,
  send: VikingBotChatResult['send'],
  ownsDraft: () => boolean,
  update: (draft: HitlDraft) => void,
) {
  return async (answers: HitlDraft['answers']) => {
    const input = formatAnswers(
      draft.group,
      draft.group.cards.map(({ id }) => answers[id]!),
    );
    update({ ...draft, pendingInput: input });
    await deliverHitlAnswer(
      send,
      input,
      () => {
        if (ownsDraft()) update({ ...draft, answers, closed: true });
      },
      () => {
        if (!ownsDraft()) return;
        update({ ...draft, failedInput: input });
        throw new Error();
      },
    );
  };
}
