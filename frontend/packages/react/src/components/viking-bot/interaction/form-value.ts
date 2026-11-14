import type {
  VikingBotInteractionAnswer,
  VikingBotInteractionRequest,
} from './types.js';

export type FormRequest = Exclude<
  VikingBotInteractionRequest,
  { kind: 'custom' | 'target' }
>;
export type FormError = 'required' | 'timeInvalid' | 'weekdayRequired';

export function initialAnswer(
  request: FormRequest,
): VikingBotInteractionAnswer | undefined {
  if (request.kind === 'choice') return undefined;
  if (request.kind === 'text')
    return { kind: 'text', value: request.initialValue ?? '' };
  if (request.kind === 'repair')
    return request.initialValue === undefined
      ? undefined
      : { kind: 'repair', value: request.initialValue };
  return { kind: 'schedule', value: request.initialValue };
}

export function validateAnswer(
  request: FormRequest,
  answer?: VikingBotInteractionAnswer,
): FormError | undefined {
  if (!answer || answer.kind !== request.kind) return 'required';
  if (request.kind === 'choice' && answer.kind === 'choice') {
    if (request.options.some((option) => option.value === answer.value)) return;
    return request.allowCustomInput && answer.value.trim()
      ? undefined
      : 'required';
  }
  if (request.kind === 'repair' && answer.kind === 'repair')
    return request.options.some((option) => option.id === answer.value)
      ? undefined
      : 'required';
  if (answer.kind !== 'schedule' || answer.value.mode === 'none') return;
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(answer.value.time))
    return 'timeInvalid';
  if (
    answer.value.mode === 'weekly' &&
    (!Number.isInteger(answer.value.weekday) ||
      answer.value.weekday < 1 ||
      answer.value.weekday > 7)
  )
    return 'weekdayRequired';
}
