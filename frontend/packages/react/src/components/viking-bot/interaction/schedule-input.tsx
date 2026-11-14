import { useId, useRef } from 'react';
import { Radio } from '../../../shared/primitives/radio/radio.js';
import {
  Select,
  SelectOption,
} from '../../../shared/primitives/select/index.js';
import { ScheduleTimeInput } from './schedule-time-input.js';
import type { FormError } from './form-value.js';
import type { Locale } from '../../../shared/locale.js';
import type { InteractionLabels } from './labels.js';
import type {
  VikingBotInteractionRequest,
  VikingBotScheduleValue,
} from './types.js';

export function ScheduleInput({
  request,
  value,
  labels,
  locale,
  disabled,
  invalid,
  errorId,
  onChange,
}: {
  request: Extract<VikingBotInteractionRequest, { kind: 'schedule' }>;
  value: VikingBotScheduleValue;
  labels: InteractionLabels;
  locale: Locale;
  disabled: boolean;
  invalid?: FormError;
  errorId?: string;
  onChange: (value: VikingBotScheduleValue) => void;
}) {
  const id = useId();
  const previous = useRef({
    time: value.mode === 'none' ? '09:00' : value.time,
    weekday: value.mode === 'weekly' ? value.weekday : 1,
  });
  const change = (next: VikingBotScheduleValue) => {
    if (disabled) return;
    onChange(next);
  };
  const changeMode = (mode: VikingBotScheduleValue['mode']) => {
    if (disabled) return;
    if (value.mode !== 'none') previous.current.time = value.time;
    if (value.mode === 'weekly') previous.current.weekday = value.weekday;
    const { time, weekday } = previous.current;
    change(
      mode === 'none'
        ? { mode }
        : mode === 'daily'
          ? { mode, time }
          : { mode, time, weekday },
    );
  };
  const modes = [
    ['daily', labels.scheduleDaily],
    ['weekly', labels.scheduleWeekly],
    ['none', labels.scheduleNone],
  ] as const;
  const weekdays = labels.weekdays.map((label, index) => ({
    value: index + 1,
    label,
  }));
  return (
    <div className="ov-viking-bot__schedule">
      <fieldset className="ov-viking-bot__schedule-frequency">
        <legend className="ov-viking-bot__interaction-label">
          {labels.scheduleFrequency}
        </legend>
        <div className="ov-viking-bot__schedule-modes">
          {modes.map(([mode, label]) => (
            <Radio
              key={mode}
              className="ov-viking-bot__schedule-mode"
              name={`${id}-mode`}
              value={mode}
              checked={value.mode === mode}
              disabled={disabled}
              onChange={() => changeMode(mode)}
            >
              {label}
            </Radio>
          ))}
        </div>
      </fieldset>
      {value.mode === 'weekly' ? (
        <div className="ov-viking-bot__interaction-field">
          <span
            id={`${id}-weekday-label`}
            className="ov-viking-bot__interaction-label"
          >
            {labels.scheduleWeekday}
          </span>
          <Select
            value={[value.weekday]}
            onChange={(selected) =>
              change({
                ...value,
                weekday: selected[0] as 1 | 2 | 3 | 4 | 5 | 6 | 7,
              })
            }
            disabled={disabled}
            locale={locale}
            aria-labelledby={`${id}-weekday-label`}
            aria-invalid={invalid === 'weekdayRequired' || undefined}
            aria-describedby={
              invalid === 'weekdayRequired' ? errorId : undefined
            }
          >
            {weekdays.map((option) => (
              <SelectOption key={option.value} value={option.value}>
                {option.label}
              </SelectOption>
            ))}
          </Select>
        </div>
      ) : null}
      {value.mode !== 'none' ? (
        <div className="ov-viking-bot__interaction-field">
          <label
            htmlFor={`${id}-time`}
            className="ov-viking-bot__interaction-label"
          >
            {labels.scheduleTime}
          </label>
          <ScheduleTimeInput
            id={`${id}-time`}
            value={value.time}
            disabled={disabled}
            labels={labels}
            timezone={request.timezone}
            invalid={invalid === 'timeInvalid'}
            errorId={errorId}
            onChange={(time) => change({ ...value, time })}
          />
        </div>
      ) : null}
    </div>
  );
}
