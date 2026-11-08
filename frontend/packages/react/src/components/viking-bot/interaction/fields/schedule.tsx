import { useState } from 'react';

import type { InteractionLabels } from '../labels.js';
import type {
  VikingBotInteractionAnswer,
  VikingBotScheduleValue,
} from '../types.js';

type Weekday = 1 | 2 | 3 | 4 | 5 | 6 | 7;

/** 定时字段。Schedule field. */
export function ScheduleField({
  initialValue,
  timezone,
  disabled,
  labels,
  onSubmit,
}: {
  initialValue: VikingBotScheduleValue;
  timezone?: string;
  disabled?: boolean;
  labels: InteractionLabels;
  onSubmit: (answer: VikingBotInteractionAnswer) => void;
}) {
  const [value, setValue] = useState<VikingBotScheduleValue>(initialValue);
  const time = value.mode === 'none' ? '09:00' : value.time;
  const weekday: Weekday = value.mode === 'weekly' ? value.weekday : 1;

  return (
    <>
      <select
        className="ov-viking-bot__interaction-input"
        value={value.mode}
        disabled={disabled}
        onChange={(event) => {
          const mode = event.target.value as VikingBotScheduleValue['mode'];
          if (mode === 'none') setValue({ mode: 'none' });
          else if (mode === 'daily') setValue({ mode: 'daily', time });
          else setValue({ mode: 'weekly', time, weekday });
        }}
      >
        <option value="none">{labels.scheduleNone}</option>
        <option value="daily">{labels.scheduleDaily}</option>
        <option value="weekly">{labels.scheduleWeekly}</option>
      </select>
      {value.mode !== 'none' ? (
        <input
          type="time"
          className="ov-viking-bot__interaction-input"
          value={time}
          disabled={disabled}
          onChange={(event) =>
            setValue(
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
          onChange={(event) =>
            setValue({
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
      {timezone ? (
        <small className="ov-viking-bot__interaction-hint">{timezone}</small>
      ) : null}
      <button
        type="button"
        className="ov-viking-bot__interaction-submit"
        disabled={disabled}
        onClick={() => onSubmit({ kind: 'schedule', value })}
      >
        {labels.submit}
      </button>
    </>
  );
}
