import { createOvClient } from '../../../client/client.js';
import { useOvClient, useOvIdentity } from '../../../client/provider.js';
import { sourceSuggestions } from './directory-validation.js';
import { useEffect, useMemo, useRef, useState } from 'react';
import type {
  VikingBotInteractionAnswer,
  VikingBotInteractionProps,
} from '../../../components/viking-bot/index.js';
import type { VikingBotMessage } from '../../../components/viking-bot/model.js';
import type { Locale } from '../../../shared/locale.js';
import { useMonitoringBinding } from '../../../shared/monitoring/context.js';
import { readData, tagData } from '../../../shared/monitoring/data.js';
import type { VikingBotChatResult } from '../chat/types.js';
import type { ConsoleHitlOptions, HitlGroup } from './types.js';
import { pendingGroup } from './protocol.js';
import { formatAnswers } from './answers.js';
import { presentCard } from './fields.js';
import type { CompilePickerScope } from './picker-fields.js';
import { summarizeMessages } from './summary.js';
import {
  clarificationFlow,
  confirmedSources,
  outputDirectories,
} from './flow.js';

interface Draft {
  owner: object;
  group: HitlGroup;
  turnUser?: VikingBotMessage;
  answers: Readonly<Record<string, VikingBotInteractionAnswer>>;
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
  pickerScope: CompilePickerScope & { customChat?: boolean } = {},
) {
  const monitoring = useMonitoringBinding();
  const inheritedClient = useOvClient('local');
  const [fallback] = useState(createOvClient);
  const requestOptions = pickerScope.requestOptions;
  const user = useOvIdentity(requestOptions).userId;
  const userId = typeof user === 'string' ? user : undefined;
  const hasConnection =
    !pickerScope.customChat ||
    requestOptions?.baseURL !== undefined ||
    requestOptions?.apiKey !== undefined ||
    requestOptions?.accountId !== undefined ||
    requestOptions?.userId !== undefined ||
    requestOptions?.resourceId !== undefined;
  const client = inheritedClient ?? (hasConnection ? fallback : undefined);
  const owner = useMemo(
    () => ({
      active: true,
      send: chat.send,
      sessionId: chat.sessionId,
      historyKey: chat.historyKey,
    }),
    [chat.send, chat.sessionId, chat.historyKey],
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
  const answers = same ? draft.answers : {};
  const turnUser = retry ? draft?.turnUser : lastUser;
  const flow = group && clarificationFlow(group, answers);
  const card = flow?.cards.find(({ id }) => !answers[id]);
  const recommendations =
    group && card
      ? outputDirectories(card, group, answers, flow?.output, userId)
      : [];
  const current = useRef({ owner, key: '', raw });
  const key = JSON.stringify([lastUser?.id, group?.id, card?.id]);
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
          requestKey: JSON.stringify([
            pickerScope.scopeKey,
            chat.sessionId,
            card.id,
          ]),
          ...presentCard(
            card,
            locale,
            { ...pickerScope, client },
            recommendations,
            sourceSuggestions(card, turnUser),
            confirmedSources(group, answers),
            userId,
          ),
          ...(card.options?.createRequest
            ? { index: Object.keys(answers).length, total: group.cards.length }
            : {}),
          onCancel: () => {
            if (isCurrent() && !lock.current)
              setDraft({ owner, group, turnUser, answers, closed: true });
          },
          onSubmit: async (answer) => {
            if (
              lock.current ||
              !isCurrent() ||
              answer.kind !== card.request.kind
            )
              return;
            lock.current = true;
            const next = { ...answers, [card.id]: answer };
            try {
              const nextFlow = clarificationFlow(group, next);
              if (nextFlow.cards.some(({ id }) => !next[id])) {
                setDraft({ owner, group, turnUser, answers: next });
                return;
              }
              const input = formatAnswers(
                group,
                group.cards.map(({ id }) => next[id] ?? nextFlow.fixed[id]!),
              );
              setDraft({
                owner,
                group,
                turnUser,
                answers,
                pendingInput: input,
              });
              const ownsDraft = () =>
                owner.active && current.current.owner === owner;
              const close = () =>
                setDraft({
                  owner,
                  group,
                  turnUser,
                  answers: next,
                  closed: true,
                });
              let delivered = false;
              const sent = await owner.send(
                { text: input, attachments: [] },
                () => {
                  delivered = true;
                  if (ownsDraft()) close();
                },
              );
              if (delivered || !ownsDraft()) return;
              if (!sent) {
                // A failed chat attempt may already have appended the user's wire message.
                // Preserve the last card only while that attempt is still the latest input.
                setDraft({
                  owner,
                  group,
                  turnUser,
                  answers,
                  failedInput: input,
                });
                throw new Error();
              }
              close();
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
