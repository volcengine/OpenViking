import type {
  VikingBotInteractionRequest,
  VikingBotScheduleValue,
} from '../../../components/viking-bot/index.js';
import type { ConsoleHitlCard } from './types.js';
import { object, text, weekdays } from './json.js';

export function builtinRequest(
  card: ConsoleHitlCard,
): VikingBotInteractionRequest | undefined {
  const { cardType, title: question, payload: p } = card;
  if (cardType === 'compile_output_type' || cardType === 'compile_skill') {
    const skill = cardType === 'compile_skill';
    const raw = skill ? (p.candidates ?? []) : p.options;
    if (!Array.isArray(raw) || (!skill && !raw.length)) return;
    const options = raw.map(object);
    if (
      options.some(
        (item) => !text(item?.label) || !text(item?.[skill ? 'uri' : 'value']),
      )
    )
      return;
    const values = options.map((item) => ({
      label: item!.label as string,
      value: item![skill ? 'uri' : 'value'] as string,
    }));
    if (new Set(values.map((item) => item.value)).size !== values.length)
      return;
    return {
      kind: 'choice',
      question,
      options: values,
      allowCustomInput: skill,
    };
  }
  if (
    [
      'compile_source',
      'compile_target',
      'compile_name',
      'compile_instruction',
    ].includes(cardType)
  ) {
    if (p.description !== undefined && typeof p.description !== 'string')
      return;
    if (
      p.suggested_value !== undefined &&
      typeof p.suggested_value !== 'string'
    )
      return;
    if (p.current_value !== undefined && typeof p.current_value !== 'string')
      return;
    if (
      p.suggested_values !== undefined &&
      (!Array.isArray(p.suggested_values) || !p.suggested_values.every(text))
    )
      return;
    return {
      kind: 'text',
      question,
      initialValue:
        cardType === 'compile_instruction'
          ? p.current_value
          : cardType === 'compile_source'
            ? ''
            : p.suggested_value,
      description: p.description,
      multiline:
        cardType === 'compile_instruction' || cardType === 'compile_source',
    };
  }
  if (cardType !== 'compile_schedule') return;
  const initial =
    p.initial_value === undefined
      ? { frequency: 'disabled' }
      : object(p.initial_value);
  if (
    !initial ||
    !['disabled', 'daily', 'weekly'].includes(String(initial.frequency))
  )
    return;
  const timezone = initial.timezone ?? 'Asia/Shanghai';
  if (!text(timezone)) return;
  try {
    new Intl.DateTimeFormat('en', { timeZone: timezone });
  } catch {
    return;
  }
  if (
    initial.frequency !== 'disabled' &&
    (typeof initial.time !== 'string' ||
      !/^([01]\d|2[0-3]):[0-5]\d$/.test(initial.time))
  )
    return;
  const weekday =
    weekdays.indexOf(initial.weekday as (typeof weekdays)[number]) + 1;
  if (initial.frequency === 'weekly' && !weekday) return;
  const initialValue: VikingBotScheduleValue =
    initial.frequency === 'disabled'
      ? { mode: 'none' }
      : initial.frequency === 'daily'
        ? { mode: 'daily', time: initial.time as string }
        : {
            mode: 'weekly',
            time: initial.time as string,
            weekday: weekday as 1 | 2 | 3 | 4 | 5 | 6 | 7,
          };
  return { kind: 'schedule', question, initialValue, timezone };
}
