import type { RefObject } from 'react';
import { Button } from '../../../shared/primitives/button/button.js';
import type { InteractionLabels } from './labels.js';

export function ScheduleTimePanel({
  panel,
  focusPanel,
  input,
  id,
  parts,
  labels,
  disabled,
  onChange,
  onClose,
}: {
  panel: RefObject<HTMLDivElement | null>;
  focusPanel: RefObject<boolean>;
  input: RefObject<HTMLInputElement | null>;
  id: string;
  parts: string[];
  labels: InteractionLabels;
  disabled: boolean;
  onChange: (value: string) => void;
  onClose: () => void;
}) {
  return (
    <div
      id={id}
      role="dialog"
      ref={(node) => {
        panel.current = node;
        if (node && focusPanel.current) {
          node.querySelector<HTMLElement>('[aria-selected="true"]')?.focus();
          focusPanel.current = false;
        }
      }}
      className="ov-viking-bot__schedule-time-panel"
      aria-label={labels.scheduleTime}
      onBlur={(event) => {
        if (
          !event.currentTarget.contains(event.relatedTarget) &&
          event.relatedTarget !== input.current
        )
          onClose();
      }}
      onMouseDownCapture={(event) => {
        if (event.button === 0) event.preventDefault();
      }}
      onKeyDownCapture={(event) => {
        if (event.key === 'Escape') {
          onClose();
          input.current?.focus();
          event.stopPropagation();
        }
        if (
          event.key !== 'ArrowDown' &&
          event.key !== 'ArrowUp' &&
          event.key !== 'Home' &&
          event.key !== 'End'
        )
          return;
        event.preventDefault();
        const buttons = Array.from(
          (event.target as HTMLElement)
            .closest('[role="listbox"]')
            ?.querySelectorAll<HTMLButtonElement>('button') ?? [],
        );
        const index = buttons.indexOf(event.target as HTMLButtonElement);
        const next =
          event.key === 'Home'
            ? 0
            : event.key === 'End'
              ? buttons.length - 1
              : (index +
                  (event.key === 'ArrowDown' ? 1 : -1) +
                  buttons.length) %
                buttons.length;
        buttons[next]?.focus();
      }}
    >
      {[labels.scheduleHour, labels.scheduleMinute].map((label, column) => (
        <div
          key={column}
          className="ov-viking-bot__schedule-time-column"
          role="listbox"
          aria-label={label}
        >
          {Array.from({ length: column === 0 ? 24 : 60 }, (_, index) =>
            String(index).padStart(2, '0'),
          ).map((part) => (
            <Button
              key={part}
              type="text"
              role="option"
              aria-selected={parts[column] === part}
              tabIndex={parts[column] === part ? 0 : -1}
              ref={
                parts[column] === part
                  ? (node) => node?.scrollIntoView?.({ block: 'nearest' })
                  : undefined
              }
              onClick={() => {
                if (!disabled)
                  onChange(
                    column === 0
                      ? `${part}:${parts[1]}`
                      : `${parts[0]}:${part}`,
                  );
              }}
            >
              {part}
            </Button>
          ))}
        </div>
      ))}
    </div>
  );
}
