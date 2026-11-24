import { useId, useRef } from 'react';
import { Radio } from '../../shared/primitives/radio/radio.js';
import { Select, SelectOption } from '../../shared/primitives/select/index.js';
import { TimePicker } from '../../shared/primitives/time-picker/time-picker.js';
import type { FileBrowserWorkflowLabels } from './preview-workflow-types.js';
import type { Locale } from '../../shared/locale.js';

export interface CompileScheduleDraft {
  mode: 'daily' | 'weekly' | 'none';
  time: string;
  weekday?: number;
}

/** Scheduled fields retain their values when the update mode is temporarily disabled. */
export function PreviewCompileSchedule({
  value,
  text,
  locale,
  disabled,
  invalid,
  onChange,
}: {
  value: CompileScheduleDraft;
  text: FileBrowserWorkflowLabels;
  locale: Locale;
  disabled: boolean;
  invalid: boolean;
  onChange: (schedule: CompileScheduleDraft) => void;
}) {
  const id = useId();
  const dayNames = new Intl.DateTimeFormat(locale, {
    weekday: 'short',
    timeZone: 'UTC',
  });
  const previous = useRef(value);
  const changeMode = (mode: CompileScheduleDraft['mode']) => {
    const draft = value.mode === 'none' ? previous.current : value;
    previous.current = draft;
    const minutes = Math.floor(Math.random() * 1440);
    const time =
      draft.time ||
      `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`;
    onChange({ ...draft, mode, time });
  };
  return (
    <div className="ov-compile-schedule">
      <fieldset className="ov-compile-schedule__frequency" disabled={disabled}>
        <legend>{text.frequency}</legend>
        <div className="ov-compile-schedule__choices">
          {(['daily', 'weekly', 'none'] as const).map((mode) => (
            <Radio
              key={mode}
              name={`${id}-frequency`}
              value={mode}
              checked={value.mode === mode}
              disabled={disabled}
              className="ov-compile-schedule__choice"
              onChange={() => changeMode(mode)}
            >
              {mode === 'none' ? text.noSchedule : text[mode]}
            </Radio>
          ))}
        </div>
      </fieldset>
      {value.mode === 'weekly' ? (
        <div className="ov-compile-schedule__field">
          <label id={`${id}-day-label`} htmlFor={`${id}-day`}>
            {text.weekday}
          </label>
          <Select<number>
            id={`${id}-day`}
            name="weekday"
            aria-labelledby={`${id}-day-label`}
            aria-required="true"
            aria-describedby={invalid ? `${id}-day-error` : undefined}
            status={invalid ? 'error' : undefined}
            size="large"
            locale={locale}
            disabled={disabled}
            placeholder={text.selectWeekday}
            value={value.weekday ? [value.weekday] : []}
            onChange={(days) => onChange({ ...value, weekday: days[0] })}
          >
            {Array.from({ length: 7 }, (_, index) => (
              <SelectOption key={index} value={index + 1}>
                {dayNames.format(new Date(Date.UTC(2024, 0, index + 1)))}
              </SelectOption>
            ))}
          </Select>
          {invalid ? (
            <p
              id={`${id}-day-error`}
              className="ov-file-browser-workflow__error"
              role="alert"
            >
              {text.weekdayRequired}
            </p>
          ) : null}
        </div>
      ) : null}
      {value.mode !== 'none' ? (
        <div className="ov-compile-schedule__field">
          <label htmlFor={`${id}-time`}>{text.time}</label>
          <TimePicker
            size="large"
            id={`${id}-time`}
            value={value.time}
            disabled={disabled}
            name="schedule-time"
            required
            pattern="([01][0-9]|2[0-3]):[0-5][0-9]"
            aria-label={text.time}
            hourLabel={text.hour}
            minuteLabel={text.minute}
            onChange={(time) => onChange({ ...value, time })}
          />
        </div>
      ) : null}
    </div>
  );
}
