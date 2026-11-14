import type { VikingBotMessage } from '../../../components/viking-bot/model.js';
import { resolveInteractionLabels } from '../../../components/viking-bot/interaction/labels.js';
import type { Locale } from '../../../shared/locale.js';
import type { ConsoleHitlOptions, HitlGroup } from './types.js';
import { groupsIn, object, parse, text, weekdays } from './protocol.js';
import { hitlLabels } from './locales.js';

const builtinTypes = new Set([
  'compile_output_type',
  'compile_source',
  'compile_target',
  'compile_name',
  'compile_skill',
  'compile_instruction',
  'compile_schedule',
]);
function readable(value: unknown): string {
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return value.map(readable).join('\n');
  const record = object(value);
  if (record)
    return Object.entries(record)
      .map(([key, item]) => `${key}: ${readable(item)}`)
      .join('\n');
  return typeof value === 'number' || typeof value === 'boolean'
    ? String(value)
    : '';
}

function answerRows(
  raw: string,
  groups: Map<string, HitlGroup>,
  locale: Locale,
  options?: ConsoleHitlOptions,
) {
  const body = object(parse(raw));
  if (
    body?.type !== 'request_user_input_response' ||
    body.schema_version !== '1.0' ||
    !text(body.tool_call_id) ||
    !Array.isArray(body.answers) ||
    !body.answers.length
  )
    return;
  const group = groups.get(body.tool_call_id);
  const labels = hitlLabels(locale);
  const interaction = resolveInteractionLabels(locale);
  const rows: { label: string; value: string }[] = [];
  for (const [index, value] of body.answers.entries()) {
    const answer = object(value);
    if (!answer || !text(answer.card_type) || !('value' in answer)) return;
    const card = group?.cards[index];
    if (card && card.cardType !== answer.card_type) return;
    if (
      !builtinTypes.has(answer.card_type) &&
      !options?.cards?.[answer.card_type] &&
      !card?.options
    )
      return;
    const label = text(answer.title)
      ? answer.title
      : (card?.title ?? answer.card_type);
    let result = readable(answer.value);
    if (card?.options?.summarize)
      result = card.options.summarize(answer.value, card);
    else if (card?.request.kind === 'choice') {
      const option = card.request.options.find(
        (item) => item.value === answer.value,
      );
      if (typeof option?.label === 'string') result = option.label;
    } else if (answer.card_type === 'compile_schedule') {
      const schedule = object(answer.value);
      if (
        !schedule ||
        !['disabled', 'daily', 'weekly'].includes(String(schedule.frequency))
      )
        return;
      if (schedule.frequency === 'disabled') result = interaction.scheduleNone;
      else {
        if (
          typeof schedule.time !== 'string' ||
          !/^([01]\d|2[0-3]):[0-5]\d$/.test(schedule.time)
        )
          return;
        const day = weekdays.indexOf(
          schedule.weekday as (typeof weekdays)[number],
        );
        if (schedule.frequency === 'weekly' && day < 0) return;
        result =
          schedule.frequency === 'weekly'
            ? labels.weekly(
                interaction.weekdays[day] ?? String(schedule.weekday),
                schedule.time,
              )
            : labels.daily(schedule.time);
        if (text(schedule.timezone) && schedule.timezone !== 'Asia/Shanghai')
          result += ` (${schedule.timezone})`;
      }
    }
    rows.push({ label, value: result || labels.empty });
  }
  return rows;
}

/** Presentation metadata never replaces the original text used by transport or storage. */
export function summarizeMessages(
  messages: readonly VikingBotMessage[],
  locale: Locale,
  options?: ConsoleHitlOptions,
) {
  const groups = new Map(
    groupsIn(messages, options).map((group) => [group.id, group]),
  );
  return messages.map((message) => {
    const part = message.parts[0];
    if (
      message.role !== 'user' ||
      message.parts.length !== 1 ||
      part?.type !== 'text'
    )
      return message;
    const answerSummary = answerRows(part.text, groups, locale, options);
    return answerSummary ? { ...message, answerSummary } : message;
  });
}
