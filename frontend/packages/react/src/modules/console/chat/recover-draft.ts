import { object, parse } from '../hitl/json.js';
import type { VikingBotDraft } from '../../../components/viking-bot/model.js';

/** The gateway accepts one message, not a history array. Recover undelivered
 * inputs in a single invocation; keep their original UI records unchanged. */
export function recoverDraft(
  undelivered: readonly VikingBotDraft[],
  draft: VikingBotDraft,
): VikingBotDraft {
  if (!undelivered.length) return draft;
  const response = object(parse(draft.text));
  if (
    response?.type === 'request_user_input_response' &&
    response.schema_version === '1.0'
  ) {
    undelivered = undelivered.filter((input) => {
      const prior = object(parse(input.text));
      return (
        prior?.type !== response.type ||
        prior?.schema_version !== response.schema_version ||
        prior?.tool_call_id !== response.tool_call_id ||
        input.attachments.length ||
        input.references?.length
      );
    });
    if (!undelivered.length) return draft;
  }
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
