import type { ReactNode } from 'react';
import type { VikingBotInteractionAnswer } from './types.js';
import type { VikingBotInteractionFields } from './field-types.js';
import type { FormError } from './form-value.js';

export type FieldErrors = Partial<
  Record<keyof VikingBotInteractionFields, ReactNode>
>;

export async function validateFields(
  fields: VikingBotInteractionFields | undefined,
  answer: VikingBotInteractionAnswer,
  signal: AbortSignal,
): Promise<FieldErrors> {
  if (answer.kind !== 'schedule') {
    return answer.kind === 'custom' || answer.kind === 'steps'
      ? {}
      : {
          value: await fields?.value?.validate?.(answer.value, answer, {
            signal,
          }),
        };
  }
  const value = answer.value;
  const [mode, time, weekday] = await Promise.all([
    fields?.mode?.validate?.(value.mode, answer, { signal }),
    value.mode !== 'none'
      ? fields?.time?.validate?.(value.time, answer, { signal })
      : undefined,
    value.mode === 'weekly'
      ? fields?.weekday?.validate?.(value.weekday, answer, { signal })
      : undefined,
  ]);
  return { mode, time, weekday };
}

/** Editing one control clears its own error, including errors from a previous submit. */
export function clearChangedErrors(
  errors: FieldErrors,
  previous: VikingBotInteractionAnswer | undefined,
  next: VikingBotInteractionAnswer,
): FieldErrors {
  if (previous?.kind !== 'schedule' || next.kind !== 'schedule') return {};
  const before = previous.value;
  const after = next.value;
  if (before.mode !== after.mode) return {};
  return Object.fromEntries(
    Object.entries(errors).filter(
      ([key]) => Reflect.get(before, key) === Reflect.get(after, key),
    ),
  );
}

export function clearChangedValidation(
  error: FormError | undefined,
  previous: VikingBotInteractionAnswer | undefined,
  next: VikingBotInteractionAnswer,
): FormError | undefined {
  const key =
    error === 'timeInvalid'
      ? 'time'
      : error === 'weekdayRequired'
        ? 'weekday'
        : 'value';
  return clearChangedErrors({ [key]: error }, previous, next)[key]
    ? error
    : undefined;
}
