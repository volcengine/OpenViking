import { useId, useRef, useState, type ComponentProps } from 'react';
import { Input } from '../../../shared/primitives/input/input.js';
import { ScheduleTimePanel } from './schedule-time-panel.js';
import { Popover } from '../../../shared/primitives/popover/popover.js';
import ClockIcon from '../../../../../icons/svg/viking-bot/interaction-clock.svg?react';
import type { InteractionLabels } from './labels.js';

function ScheduleTimeControl({
  id,
  value,
  onChange,
  labels,
  disabled,
  timezone,
  invalid,
  errorId,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  labels: InteractionLabels;
  disabled: boolean;
  timezone?: string;
  invalid: boolean;
  errorId?: string;
}) {
  const [open, setOpen] = useState(false);
  const panel = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const focusPanel = useRef(false);
  const popupId = useId();
  const parts = /^([01]\d|2[0-3]):[0-5]\d$/.test(value)
    ? value.split(':')
    : ['09', '00'];
  return (
    <>
      <Popover
        role="presentation"
        trigger="click"
        placement="bottom"
        alignment="start"
        open={open}
        onOpenChange={setOpen}
        disabled={disabled}
        className="ov-viking-bot__schedule-popup"
        content={
          <ScheduleTimePanel
            panel={panel}
            focusPanel={focusPanel}
            input={input}
            id={popupId}
            parts={parts}
            labels={labels}
            disabled={disabled}
            onChange={onChange}
            onClose={() => setOpen(false)}
          />
        }
      >
        <div className="ov-viking-bot__schedule-time-trigger">
          <Input
            ref={input}
            id={id}
            name="schedule-time"
            value={value}
            required
            pattern="([01][0-9]|2[0-3]):[0-5][0-9]"
            placeholder="HH:mm"
            disabled={disabled}
            aria-invalid={invalid || undefined}
            aria-describedby={
              [timezone ? `${id}-timezone` : '', invalid ? errorId : '']
                .filter(Boolean)
                .join(' ') || undefined
            }
            aria-controls={open && !disabled ? popupId : undefined}
            aria-expanded={open && !disabled}
            aria-haspopup="dialog"
            suffix={<ClockIcon className="ov-viking-bot__schedule-clock" />}
            onBlur={(event) => {
              if (!panel.current?.contains(event.relatedTarget)) setOpen(false);
            }}
            onChange={(event) => onChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Escape') setOpen(false);
              if (
                event.key === 'Enter' &&
                open &&
                !event.nativeEvent.isComposing
              ) {
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
      {timezone ? (
        <span id={`${id}-timezone`} className="ov-viking-bot__interaction-sr">
          {timezone}
        </span>
      ) : null}
    </>
  );
}

export function ScheduleTimeInput(
  props: ComponentProps<typeof ScheduleTimeControl>,
) {
  return <ScheduleTimeControl key={String(props.disabled)} {...props} />;
}
