import type { VikingBotDraft } from '../../../components/viking-bot/model.js';

/** The gateway accepts one message, not a history array. Recover undelivered
 * inputs in a single invocation; keep their original UI records unchanged. */
export function recoverDraft(
  undelivered: readonly VikingBotDraft[],
  draft: VikingBotDraft,
): VikingBotDraft {
  if (!undelivered.length) return draft;
  return {
    text: [
      'Earlier user inputs (context only; do not answer them separately):',
      JSON.stringify(undelivered.map((input) => input.text)),
      'Current user input:',
      draft.text,
    ].join('\n\n'),
    attachments: [
      ...undelivered.flatMap((input) => input.attachments),
      ...draft.attachments,
    ],
    references: [
      ...undelivered.flatMap((input) => input.references ?? []),
      ...(draft.references ?? []),
    ],
  };
}
