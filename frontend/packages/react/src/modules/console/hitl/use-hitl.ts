import { sourceSuggestions } from './directory-validation.js';
import { useRef, useState } from 'react';
import type { VikingBotInteractionProps } from '../../../components/viking-bot/index.js';
import type { Locale } from '../../../shared/locale.js';
import { useMonitoringBinding } from '../../../shared/monitoring/context.js';
import { readData, tagData } from '../../../shared/monitoring/data.js';
import type { VikingBotChatResult } from '../chat/types.js';
import type { ConsoleHitlOptions } from './types.js';
import { pendingGroup } from './protocol.js';
import { useHitlScope } from './use-hitl-scope.js';
import { createHitlDelivery, type HitlDraft } from './delivery.js';
import { validateWikiConfirmation, wikiConfirmation } from './confirmation.js';
import { hitlLabels } from './locales.js';
import { confirmationInteraction } from '../../../components/viking-bot/confirmation/viking-bot-confirmation.js';
import { resolveInteractionLabels } from '../../../components/viking-bot/interaction/labels.js';
import { presentCard } from './fields.js';
import type { CompilePickerScope } from './picker-fields.js';
import { summarizeMessages } from './summary.js';
import {
  clarificationFlow,
  confirmedSources,
  outputDirectories,
} from './flow.js';

/** A tool call belongs to one controller, session and user turn. */
export function useHitl(
  chat: VikingBotChatResult,
  enabled: boolean,
  locale: Locale,
  options?: ConsoleHitlOptions | false,
  pickerScope: CompilePickerScope & { customChat?: boolean } = {},
) {
  const monitoring = useMonitoringBinding();
  const requestOptions = pickerScope.requestOptions;
  const { client, owner, userId } = useHitlScope(
    chat,
    requestOptions,
    pickerScope.customChat,
  );
  const [draft, setDraft] = useState<HitlDraft>();
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
  const current = useRef({ owner, key: '' });
  const key = JSON.stringify([lastUser?.id, group?.id, card?.id]);
  current.current = { owner, key };
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
  const draftState = group && {
    owner,
    group,
    turnUser,
    answers,
    manual: same ? draft.manual : undefined,
  };
  const requestKey = JSON.stringify([
    pickerScope.scopeKey,
    chat.sessionId,
    card?.id,
  ]);
  const index = Object.keys(answers).length;
  const total = group?.cards.length ?? 0;
  const wikiDefaults =
    active && group && client
      ? wikiConfirmation(group, turnUser, locale, userId)
      : undefined;
  const defaults =
    !draftState?.manual && index === 0 ? wikiDefaults : undefined;
  const deliver =
    draftState &&
    createHitlDelivery(
      draftState,
      owner.send,
      () => owner.active && current.current.owner === owner,
      setDraft,
    );
  const confirmation =
    defaults && draftState && client && deliver
      ? confirmationInteraction({
          requestKey,
          title: hitlLabels(locale).confirmationTitle,
          rows: defaults.rows,
          locale,
          onEdit: () => {
            if (isCurrent() && !owner.locked)
              setDraft({ ...draftState, manual: true });
          },
          onConfirm: async () => {
            if (!isCurrent() || owner.locked) return;
            owner.locked = true;
            try {
              const signal =
                requestOptions?.signal ?? new AbortController().signal;
              await validateWikiConfirmation(
                defaults,
                client,
                requestOptions,
                signal,
                locale,
                userId,
              );
              if (isCurrent()) await deliver(defaults.answers);
            } finally {
              owner.locked = false;
            }
          },
        })
      : undefined;
  const interaction: VikingBotInteractionProps | undefined =
    !active || !group || !card || !draftState || !deliver
      ? undefined
      : {
          requestKey,
          ...presentCard(
            card,
            locale,
            { ...pickerScope, client },
            recommendations,
            card.cardType === 'compile_source' &&
              draftState.manual &&
              wikiDefaults
              ? wikiDefaults.sources
              : sourceSuggestions(card, turnUser),
            confirmedSources(group, answers),
            userId,
          ),
          index,
          total,
          ...(!card.options?.createRequest && index < total - 1
            ? { labels: { submit: resolveInteractionLabels(locale).next } }
            : {}),
          onCancel: () => {
            if (isCurrent() && !owner.locked)
              setDraft({ ...draftState, closed: true });
          },
          onSubmit: async (answer) => {
            if (
              owner.locked ||
              !isCurrent() ||
              answer.kind !== card.request.kind
            )
              return;
            const next = { ...answers, [card.id]: answer };
            if (group.cards.some(({ id }) => !next[id])) {
              setDraft({ ...draftState, answers: next });
              return;
            }
            owner.locked = true;
            try {
              await deliver(next);
            } finally {
              owner.locked = false;
            }
          },
        };
  const messages = summarizeMessages(chat.messages, locale, config);
  tagData(monitoring, messages, readData(monitoring, [chat.messages]));
  return { interaction: confirmation ?? interaction, messages };
}
