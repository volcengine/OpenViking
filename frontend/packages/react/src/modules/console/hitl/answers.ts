import type { VikingBotInteractionAnswer } from '../../../components/viking-bot/index.js';
import type { HitlCard, HitlGroup } from './types.js';
import {
  compileTargetError,
  parseCompileUri,
  parseSourceUris,
  splitSourceLines,
} from './uris.js';
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
  if (card.cardType === 'compile_source' && answer.kind === 'text') {
    // A replacement card owns its validation; preserve its prior string-list payload.
    if (card.options?.createRequest) return splitSourceLines(answer.value);
    const sources = parseSourceUris(answer.value);
    if (!sources?.length) throw new Error('Invalid compile source directories');
    return sources;
  }
  if (card.cardType === 'compile_target' && answer.kind === 'text') {
    const target = parseCompileUri(answer.value);
    // Host text may carry presets, while valid URI answers keep canonical wire formatting.
    if (card.options?.createRequest) return target ?? answer.value;
    if (!target || compileTargetError(target))
      throw new Error('Invalid compile output directory');
    return target;
  }
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
