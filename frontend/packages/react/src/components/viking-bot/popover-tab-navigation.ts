import type { KeyboardEvent } from 'react';

/** Match the native popover's logical Tab order across the portal boundary. */
export function navigatePopoverTab(
  event: KeyboardEvent<HTMLElement>,
  anchor: HTMLElement | null,
  surface: HTMLElement | null,
  close: () => void,
) {
  if (event.key !== 'Tab' || event.altKey || event.ctrlKey || event.metaKey)
    return;
  const trigger = anchor?.querySelector('button');
  const controls = [
    ...(surface?.querySelectorAll<HTMLElement>(
      'button, a[href], input, select, textarea, [tabindex], [contenteditable="true"]',
    ) ?? []),
  ].filter((element) => element.tabIndex >= 0 && !element.matches(':disabled'));
  const first = controls[0];
  const last = controls.at(-1);
  const destination =
    !event.shiftKey && event.target === trigger
      ? first
      : event.shiftKey && event.target === first
        ? trigger
        : undefined;
  if (destination) {
    event.preventDefault();
    event.stopPropagation();
    destination.focus();
  } else if (!event.shiftKey && event.target === last) {
    // Resume the browser's default forward Tab from the trigger, allowing focus
    // to leave this non-modal popup in its original document position.
    event.stopPropagation();
    close();
  }
}
