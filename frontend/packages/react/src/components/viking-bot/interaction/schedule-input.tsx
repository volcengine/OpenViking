import { useId, useRef } from 'react';
import { Radio } from '../../../shared/primitives/radio/radio.js';
import { InteractionField } from './interaction-field.js';
import type { VikingBotInteractionFields } from './field-types.js';
import type { FieldErrors } from './field-validation.js';
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
  fields,
  fieldErrors,
  onChange,
}: {
  request: Extract<VikingBotInteractionRequest, { kind: 'schedule' }>;
  value: VikingBotScheduleValue;
  labels: InteractionLabels;
  locale: Locale;
  disabled: boolean;
  invalid?: FormError;
  fields?: VikingBotInteractionFields;
  fieldErrors?: FieldErrors;
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
      <InteractionField
        id={`${id}-mode`}
        label={labels.scheduleFrequency}
        group
        config={fields?.mode}
        value={value.mode}
        onChange={changeMode}
        disabled={disabled}
        error={fieldErrors?.mode}
      >
        {(field) => (
          <div
            {...field}
            role="radiogroup"
            className="ov-viking-bot__schedule-modes"
          >
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
        )}
      </InteractionField>
      {value.mode === 'weekly' ? (
        <InteractionField
          id={`${id}-weekday`}
          label={labels.scheduleWeekday}
          config={fields?.weekday}
          value={value.weekday}
          onChange={(weekday) => change({ ...value, weekday })}
          disabled={disabled}
          error={
            invalid === 'weekdayRequired'
              ? labels.weekdayRequired
              : fieldErrors?.weekday
          }
        >
          {(field) => (
            <Select
              {...field}
              value={[value.weekday]}
              onChange={(selected) =>
                change({
                  ...value,
                  weekday: selected[0] as 1 | 2 | 3 | 4 | 5 | 6 | 7,
                })
              }
              disabled={disabled}
              locale={locale}
            >
              {weekdays.map((option) => (
                <SelectOption key={option.value} value={option.value}>
                  {option.label}
                </SelectOption>
              ))}
            </Select>
          )}
        </InteractionField>
      ) : null}
      {value.mode !== 'none' ? (
        <InteractionField
          id={`${id}-time`}
          label={labels.scheduleTime}
          config={fields?.time}
          value={value.time}
          onChange={(time) => change({ ...value, time })}
          disabled={disabled}
          error={
            invalid === 'timeInvalid' ? labels.timeInvalid : fieldErrors?.time
          }
        >
          {(field) => (
            <ScheduleTimeInput
              controlProps={field}
              id={field.id}
              value={value.time}
              disabled={disabled}
              labels={labels}
              timezone={request.timezone}
              invalid={Boolean(field['aria-invalid'])}
              errorId={field['aria-describedby']}
              onChange={(time) => change({ ...value, time })}
            />
          )}
        </InteractionField>
      ) : null}
    </div>
  );
}
