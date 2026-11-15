/**
 * Adapted from Arco Design TimePicker/picker.tsx (MIT), commit
 * 4e087a0fe7d5ae2f222fbce4a9518ba047b01772. See LICENSE.arco-design.
 * Keeps editable HH:mm input, clickToClose=false and immediate panel selection.
 * Uses local Input/Popover primitives; no Arco runtime or ConfigProvider dependency.
 * https://github.com/arco-design/arco-design/blob/4e087a0fe7d5ae2f222fbce4a9518ba047b01772/components/TimePicker/picker.tsx
 */
import { useId, useRef, useState } from 'react';
import { Input, type InputProps } from '../input/input.js';
import { Popover } from '../popover/popover.js';
import { TimePickerPanel } from './time-picker-panel.js';
import ClockIcon from '../../../../../icons/svg/viking-bot/interaction-clock.svg?react';

export interface TimePickerProps extends Omit<
  InputProps,
  'value' | 'defaultValue' | 'onChange' | 'type' | 'suffix'
> {
  value: string;
  onChange: (value: string) => void;
  'aria-label': string;
  hourLabel: string;
  minuteLabel: string;
}

function TimePickerControl({
  value,
  onChange,
  hourLabel,
  minuteLabel,
  disabled,
  onBlur,
  onKeyDown,
  ...props
}: TimePickerProps) {
  const [open, setOpen] = useState(false);
  const panel = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const focusPanel = useRef(false);
  const popupId = useId();
  return (
    <Popover
      role="presentation"
      trigger="click"
      clickToClose={false}
      placement="bottom"
      alignment="start"
      open={open}
      onOpenChange={setOpen}
      disabled={disabled}
      className="ov-time-picker__popup"
      content={
        <TimePickerPanel
          id={popupId}
          value={value}
          panel={panel}
          input={input}
          focusPanel={focusPanel}
          label={props['aria-label']}
          hourLabel={hourLabel}
          minuteLabel={minuteLabel}
          onChange={onChange}
          onClose={() => setOpen(false)}
        />
      }
    >
      <div className="ov-time-picker">
        <Input
          {...props}
          ref={input}
          value={value}
          placeholder={props.placeholder ?? 'HH:mm'}
          disabled={disabled}
          aria-controls={open ? popupId : undefined}
          aria-expanded={open}
          aria-haspopup="dialog"
          suffix={<ClockIcon className="ov-time-picker__clock" />}
          onBlur={(event) => {
            if (!panel.current?.contains(event.relatedTarget)) setOpen(false);
            onBlur?.(event);
          }}
          onChange={(event) => {
            setOpen(true);
            onChange(event.target.value);
          }}
          onKeyDown={(event) => {
            onKeyDown?.(event);
            if (event.defaultPrevented || event.nativeEvent.isComposing) return;
            if (event.key === 'Escape' && open) {
              event.preventDefault();
              event.stopPropagation();
              setOpen(false);
            }
            if (event.key === 'Enter' && open) {
              event.preventDefault();
              setOpen(false);
            }
            if (event.key === 'ArrowDown') {
              event.preventDefault();
              focusPanel.current = true;
              setOpen(true);
              if (panel.current) {
                panel.current
                  .querySelector<HTMLElement>('[aria-selected="true"]')
                  ?.focus();
                focusPanel.current = false;
              }
            }
          }}
        />
      </div>
    </Popover>
  );
}

export function TimePicker(props: TimePickerProps) {
  return <TimePickerControl key={String(props.disabled)} {...props} />;
}
