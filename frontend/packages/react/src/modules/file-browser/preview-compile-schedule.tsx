import { Input } from '../../shared/primitives/input/input.js';
import { useId } from 'react';
import type {
  CompileConfig,
  FileBrowserWorkflowLabels,
} from './preview-workflow-types.js';
import type { Locale } from '../../shared/locale.js';
export function PreviewCompileSchedule({
  value,
  text,
  locale,
  onChange,
}: {
  value: CompileConfig['update_schedule'];
  text: FileBrowserWorkflowLabels;
  locale: Locale;
  onChange: (schedule: CompileConfig['update_schedule']) => void;
}) {
  const id = useId();
  const dayNames = new Intl.DateTimeFormat(locale, {
    weekday: 'long',
    timeZone: 'UTC',
  });
  return (
    <>
      <label htmlFor={`${id}-frequency`}>{text.schedule}</label>
      <select
        id={`${id}-frequency`}
        value={value?.frequency ?? ''}
        onChange={(event) => {
          const frequency = event.target.value as 'daily' | 'weekly' | '';
          onChange(
            frequency
              ? {
                  frequency,
                  hour: value?.hour ?? 0,
                  minute: value?.minute ?? 0,
                  ...(frequency === 'weekly'
                    ? { weekday: value?.weekday ?? 1 }
                    : {}),
                }
              : null,
          );
        }}
      >
        <option value="">{text.noSchedule}</option>
        <option value="daily">{text.daily}</option>
        <option value="weekly">{text.weekly}</option>
      </select>
      {value ? (
        <>
          <label htmlFor={`${id}-time`}>{text.time}</label>
          <Input
            id={`${id}-time`}
            type="time"
            required
            value={`${String(value.hour).padStart(2, '0')}:${String(value.minute).padStart(2, '0')}`}
            onChange={(event) => {
              const [hour, minute] = event.target.value.split(':').map(Number);
              if (hour !== undefined && minute !== undefined)
                onChange({ ...value, hour, minute });
            }}
          />
          {value.frequency === 'weekly' ? (
            <>
              <label htmlFor={`${id}-day`}>{text.weekday}</label>
              <select
                id={`${id}-day`}
                value={value.weekday ?? 1}
                onChange={(event) =>
                  onChange({ ...value, weekday: Number(event.target.value) })
                }
              >
                {Array.from({ length: 7 }, (_, index) => (
                  <option key={index} value={index + 1}>
                    {dayNames.format(new Date(Date.UTC(2024, 0, index + 1)))}
                  </option>
                ))}
              </select>
            </>
          ) : null}
        </>
      ) : null}
    </>
  );
}
