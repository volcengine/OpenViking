import type { FormError } from './form-value.js';

export function focusError(form: HTMLFormElement | null) {
  const invalid = form?.querySelector<HTMLElement>('[aria-invalid="true"]');
  const selector =
    'input:not(:disabled), textarea:not(:disabled), button:not(:disabled), [tabindex]';
  const target = invalid?.matches(selector)
    ? invalid
    : invalid?.querySelector<HTMLElement>(selector);
  target?.focus();
}

export function focusInvalidAnswer(
  form: HTMLFormElement | null,
  invalid?: FormError,
) {
  const selector =
    invalid === 'timeInvalid'
      ? '[name="schedule-time"]'
      : invalid === 'weekdayRequired'
        ? '[role="combobox"]'
        : form?.querySelector('[name="custom-answer"]')
          ? '[name="custom-answer"]'
          : 'input:not(:disabled), .ov-viking-bot__interaction-picker-trigger:not(:disabled)';
  form?.querySelector<HTMLElement>(selector)?.focus();
}
