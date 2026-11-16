import type { VikingBotInteractionAnswer } from '../../../components/viking-bot/index.js';
import type { HitlCard, HitlGroup } from './types.js';
import { weekdays } from './json.js';

function wireValue(card: HitlCard, answer: VikingBotInteractionAnswer) {
  if (card.options?.serialize) return card.options.serialize(answer, card);
  if (answer.kind === 'schedule')
    return answer.value.mode === 'none'
      ? { frequency: 'disabled' }
      : {
          frequency: answer.value.mode,
          time: answer.value.time,
          timezone:
            card.request.kind === 'schedule'
              ? card.request.timezone
              : undefined,
          ...(answer.value.mode === 'weekly'
            ? { weekday: weekdays[answer.value.weekday - 1] }
            : {}),
        };
  if (card.cardType === 'compile_source' && answer.kind === 'text')
    return [
      ...new Set(
        answer.value
          .split(/\r?\n/)
          .map((uri) => uri.trim())
          .filter(Boolean),
      ),
    ];
  return answer.value;
}

export function formatAnswers(
  group: HitlGroup,
  answers: readonly VikingBotInteractionAnswer[],
) {
  if (
    answers.length !== group.cards.length ||
    answers.some(
      (answer, index) => answer.kind !== group.cards[index]?.request.kind,
    )
  )
    throw new Error('Answers do not match the requested cards');
  return JSON.stringify({
    type: 'request_user_input_response',
    schema_version: '1.0',
    tool_call_id: group.id,
    answers: group.cards.map((card, index) => ({
      card_type: card.cardType,
      title: card.title,
      value: wireValue(card, answers[index]!),
    })),
  });
}
