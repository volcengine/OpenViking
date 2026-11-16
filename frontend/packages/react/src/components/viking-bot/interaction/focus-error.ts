export function focusError(form: HTMLFormElement | null) {
  const invalid = form?.querySelector<HTMLElement>('[aria-invalid="true"]');
  const selector =
    'input:not(:disabled), textarea:not(:disabled), button:not(:disabled), [tabindex]';
  const target = invalid?.matches(selector)
    ? invalid
    : invalid?.querySelector<HTMLElement>(selector);
  target?.focus();
}
