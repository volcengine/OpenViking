/**
 * Adapted from Arco Design TimePicker/time-picker.tsx (MIT), commit
 * 4e087a0fe7d5ae2f222fbce4a9518ba047b01772. See LICENSE.arco-design.
 * Retains the HH:mm hour/minute columns and immediate selection (disableConfirm).
 * Omits seconds, AM/PM, ranges, disabled-time rules and footer actions.
 * https://github.com/arco-design/arco-design/blob/4e087a0fe7d5ae2f222fbce4a9518ba047b01772/components/TimePicker/time-picker.tsx
 */
import { useRef, type RefObject } from 'react';
import dayjs from 'dayjs';
import { TimeColumn } from './time-column.js';

export function TimePickerPanel({
  value,
  panel,
  input,
  focusPanel,
  id,
  label,
  hourLabel,
  minuteLabel,
  onChange,
  onClose,
}: {
  value: string;
  panel: RefObject<HTMLDivElement | null>;
  input: RefObject<HTMLInputElement | null>;
  focusPanel: RefObject<boolean>;
  id: string;
  label: string;
  hourLabel: string;
  minuteLabel: string;
  onChange: (value: string) => void;
  onClose: () => void;
}) {
  const selected = dayjs(
    `2000-01-01T${/^([01]\d|2[0-3]):[0-5]\d$/.test(value) ? value : '00:00'}`,
  );
  // 滚轮回调可能在同一次提交前交错执行，先合并选择，再在 DOM 提交时对齐受控值。
  const selection = useRef({ value: selected, onChange });
  return (
    <div
      id={id}
      role="dialog"
      aria-label={label}
      className="ov-time-picker__panel"
      ref={(node) => {
        panel.current = node;
        if (node) selection.current = { value: selected, onChange };
        if (node && focusPanel.current) {
          node.querySelector<HTMLElement>('[aria-selected="true"]')?.focus();
          focusPanel.current = false;
        }
      }}
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
        if (event.key !== 'Escape') return;
        event.stopPropagation();
        onClose();
        input.current?.focus();
      }}
    >
      {(['hour', 'minute'] as const).map((unit) => (
        <TimeColumn
          key={unit}
          label={unit === 'hour' ? hourLabel : minuteLabel}
          value={selected.get(unit)}
          list={Array.from(
            { length: unit === 'hour' ? 24 : 60 },
            (_, value) => ({
              value,
              label: String(value).padStart(2, '0'),
              selected: selected.get(unit) === value,
            }),
          )}
          onSelect={(next) => {
            selection.current.value = selection.current.value.set(unit, next);
            selection.current.onChange(selection.current.value.format('HH:mm'));
          }}
        />
      ))}
    </div>
  );
}
