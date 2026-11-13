import { Input } from '../../../shared/primitives/input/input.js';
import { useState } from 'react';

import type { InteractionLabels } from './labels.js';
import type {
  VikingBotInteractionRequest,
  VikingBotScheduleValue,
} from './types.js';

type ScheduleRequest = Extract<
  VikingBotInteractionRequest,
  { kind: 'schedule' }
>;
type Weekday = 1 | 2 | 3 | 4 | 5 | 6 | 7;

const DEFAULT_TIME = '09:00';

/** 定时输入。Schedule input. */
export function ScheduleInput({
  request,
  labels,
  disabled,
  onChange,
}: {
  request: ScheduleRequest;
  labels: InteractionLabels;
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
        aria-label={labels.scheduleWeekly}
        onChange={(event) => {
          const mode = event.target.value as VikingBotScheduleValue['mode'];
          if (mode === 'none') update({ mode: 'none' });
          else if (mode === 'daily') update({ mode: 'daily', time });
          else update({ mode: 'weekly', time, weekday });
        }}
      >
        <option value="none">{labels.scheduleNone}</option>
        <option value="daily">{labels.scheduleDaily}</option>
        <option value="weekly">{labels.scheduleWeekly}</option>
      </select>
      {value.mode !== 'none' ? (
        <Input
          type="time"
          className="ov-viking-bot__interaction-input"
          value={time}
          disabled={disabled}
          aria-label={labels.scheduleTime}
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
          aria-label={labels.scheduleWeekday}
          onChange={(event) =>
            update({
              mode: 'weekly',
              time,
              weekday: Number(event.target.value) as Weekday,
            })
          }
        >
          {labels.weekdays.map((name, index) => (
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
