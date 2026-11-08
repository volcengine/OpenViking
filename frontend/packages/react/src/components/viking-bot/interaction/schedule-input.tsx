import { useState } from 'react';

import type {
  VikingBotInteractionRequest,
  VikingBotScheduleValue,
} from './types.js';

const WEEKDAYS = [
  'monday',
  'tuesday',
  'wednesday',
  'thursday',
  'friday',
  'saturday',
  'sunday',
] as const;

type ScheduleRequest = Extract<
  VikingBotInteractionRequest,
  { kind: 'schedule' }
>;
type Weekday = 1 | 2 | 3 | 4 | 5 | 6 | 7;

const DEFAULT_TIME = '09:00';

/** 定时输入。Schedule input. */
export function ScheduleInput({
  request,
  disabled,
  onChange,
}: {
  request: ScheduleRequest;
  disabled?: boolean;
  onChange: (value: unknown) => void;
}) {
  const [value, setValue] = useState<VikingBotScheduleValue>(
    request.initialValue,
  );
  const time = value.mode === 'none' ? DEFAULT_TIME : value.time;
  const weekday: Weekday = value.mode === 'weekly' ? value.weekday : 1;

  const update = (next: VikingBotScheduleValue) => {
    setValue(next);
    onChange(next);
  };

  return (
    <div className="ov-viking-bot__schedule">
      <select
        className="ov-viking-bot__interaction-input"
        value={value.mode}
        disabled={disabled}
        aria-label="schedule frequency"
        onChange={(event) => {
          const mode = event.target.value as VikingBotScheduleValue['mode'];
          if (mode === 'none') update({ mode: 'none' });
          else if (mode === 'daily') update({ mode: 'daily', time });
          else update({ mode: 'weekly', time, weekday });
        }}
      >
        <option value="none">Do not schedule</option>
        <option value="daily">Every day</option>
        <option value="weekly">Every week</option>
      </select>
      {value.mode !== 'none' ? (
        <input
          type="time"
          className="ov-viking-bot__interaction-input"
          value={time}
          disabled={disabled}
          aria-label="schedule time"
          onChange={(event) =>
            update(
              value.mode === 'weekly'
                ? { mode: 'weekly', time: event.target.value, weekday }
                : { mode: 'daily', time: event.target.value },
            )
          }
        />
      ) : null}
      {value.mode === 'weekly' ? (
        <select
          className="ov-viking-bot__interaction-input"
          value={weekday}
          disabled={disabled}
          aria-label="schedule weekday"
          onChange={(event) =>
            update({
              mode: 'weekly',
              time,
              weekday: Number(event.target.value) as Weekday,
            })
          }
        >
          {WEEKDAYS.map((name, index) => (
            <option key={name} value={index + 1}>
              {name}
            </option>
          ))}
        </select>
      ) : null}
      {request.timezone ? (
        <small className="ov-viking-bot__interaction-hint">
          {request.timezone}
        </small>
      ) : null}
    </div>
  );
}
