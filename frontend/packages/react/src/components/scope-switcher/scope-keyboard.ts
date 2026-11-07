import type { KeyboardEvent } from 'react';

export function moveScopeFocus(event: KeyboardEvent<HTMLDivElement>) {
  if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
  const target = event.target as HTMLElement;
  const isSearch = target.matches('input.ov-scope-switcher__search');
  if (
    event.defaultPrevented ||
    (!target.matches('[role="option"]') && !isSearch) ||
    (isSearch && !event.key.startsWith('Arrow'))
  )
    return;
  const options = Array.from(
    event.currentTarget.querySelectorAll<HTMLElement>(
      '[role="option"]:not([aria-disabled="true"])',
    ),
  );
  if (!options.length) return;
  event.preventDefault();
  const current = options.indexOf(document.activeElement as HTMLElement);
  const next =
    event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? options.length - 1
        : event.key === 'ArrowDown'
          ? (current + 1) % options.length
          : current < 0
            ? options.length - 1
            : (current - 1 + options.length) % options.length;
  options[next]?.focus();
}
