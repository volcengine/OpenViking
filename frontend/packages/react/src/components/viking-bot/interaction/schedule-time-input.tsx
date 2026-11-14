import { TimePicker } from '../../../shared/primitives/time-picker/time-picker.js';
import type { InteractionLabels } from './labels.js';

export function ScheduleTimeInput({
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
  return (
    <>
      <TimePicker
        id={id}
        name="schedule-time"
        value={value}
        onChange={onChange}
        disabled={disabled}
        required
        pattern="([01][0-9]|2[0-3]):[0-5][0-9]"
        aria-label={labels.scheduleTime}
        hourLabel={labels.scheduleHour}
        minuteLabel={labels.scheduleMinute}
        aria-invalid={invalid || undefined}
        aria-describedby={
          [timezone ? `${id}-timezone` : '', invalid ? errorId : '']
            .filter(Boolean)
            .join(' ') || undefined
        }
      />
      {timezone ? (
        <span id={`${id}-timezone`} className="ov-viking-bot__interaction-sr">
          {timezone}
        </span>
      ) : null}
    </>
  );
}
