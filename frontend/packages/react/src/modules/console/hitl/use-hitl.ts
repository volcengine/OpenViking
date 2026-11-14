import { useEffect, useMemo, useRef, useState } from 'react';
import type {
  VikingBotInteractionAnswer,
  VikingBotInteractionProps,
} from '../../../components/viking-bot/index.js';
import type { Locale } from '../../../shared/locale.js';
import { useMonitoringBinding } from '../../../shared/monitoring/context.js';
import { readData, tagData } from '../../../shared/monitoring/data.js';
import type { VikingBotChatResult } from '../chat/types.js';
import type { ConsoleHitlOptions, HitlGroup } from './types.js';
import { pendingGroup } from './protocol.js';
import { formatAnswers } from './answers.js';
import { cardFields } from './fields.js';
import { summarizeMessages } from './summary.js';

interface Draft {
  owner: object;
  group: HitlGroup;
  answers: readonly VikingBotInteractionAnswer[];
  closed?: boolean;
  failedInput?: string;
  pendingInput?: string;
}

/** A tool call belongs to one controller, session and user turn. */
export function useHitl(
  chat: VikingBotChatResult,
  enabled: boolean,
  locale: Locale,
  options?: ConsoleHitlOptions | false,
) {
  const monitoring = useMonitoringBinding();
  const owner = useMemo(
    () => ({ active: true, send: chat.send, sessionId: chat.sessionId }),
    [chat.send, chat.sessionId],
  );
  useEffect(() => {
    owner.active = true;
    return () => {
      owner.active = false;
    };
  }, [owner]);
  const [draft, setDraft] = useState<Draft>();
  const lock = useMemo(() => ({ current: false, owner }), [owner]);
  const config = options === false ? undefined : options;
  const lastUser = [...chat.messages]
    .reverse()
    .find((message) => message.role === 'user');
  const raw =
    lastUser?.parts[0]?.type === 'text' ? lastUser.parts[0].text : undefined;
  const detected = pendingGroup(chat.messages, config);
  const retry =
    draft?.owner === owner &&
    (draft.failedInput === raw || draft.pendingInput === raw) &&
    raw !== undefined
      ? draft.group
      : undefined;
  const group = detected ?? retry;
  const same = draft?.owner === owner && draft.group.id === group?.id;
  const answers = same ? draft.answers : [];
  const card = group?.cards[answers.length];
  const current = useRef({ owner, key: '', raw });
  const key = JSON.stringify([lastUser?.id, group?.id, answers.length]);
  current.current = { owner, key, raw };
  const isCurrent = () =>
    owner.active &&
    current.current.owner === owner &&
    current.current.key === key;
  const active =
    enabled &&
    options !== false &&
    Boolean(group && card) &&
    !(same && draft.closed) &&
    (Boolean(retry) || (chat.status === 'idle' && chat.responseComplete));
  const interaction: VikingBotInteractionProps | undefined =
    !active || !group || !card
      ? undefined
      : {
          requestKey: `${group.id}:${answers.length}`,
          request: card.request,
          fields: cardFields(card, locale),
          index: answers.length,
          total: group.cards.length,
          onCancel: () => {
            if (isCurrent() && !lock.current)
              setDraft({ owner, group, answers, closed: true });
          },
          onSubmit: async (answer) => {
            if (
              lock.current ||
              !isCurrent() ||
              answer.kind !== card.request.kind
            )
              return;
            lock.current = true;
            const next = [...answers, answer];
            try {
              if (next.length < group.cards.length) {
                setDraft({ owner, group, answers: next });
                return;
              }
              const input = formatAnswers(group, next);
              setDraft({ owner, group, answers, pendingInput: input });
              const sent = await owner.send({ text: input, attachments: [] });
              if (!owner.active || current.current.owner !== owner) return;
              if (!sent) {
                // A failed chat attempt may already have appended the user's wire message.
                // Preserve the last card only while that attempt is still the latest input.
                setDraft({ owner, group, answers, failedInput: input });
                throw new Error();
              }
              setDraft({ owner, group, answers: next, closed: true });
            } finally {
              lock.current = false;
            }
          },
        };
  const messages = summarizeMessages(chat.messages, locale, config);
  tagData(monitoring, messages, readData(monitoring, [chat.messages]));
  return {
    interaction,
    messages,
  };
}
