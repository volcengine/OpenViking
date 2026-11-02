import type { UpdateSchedule } from '#/features/workbench/compile/lib/api'
import { useTranslation } from 'react-i18next'

const timeParts = ['hour', 'minute'] as const

export const defaultSchedule: UpdateSchedule = {
  frequency: 'daily',
  hour: 9,
  minute: 0,
}

export function ScheduleFields({
  enabled,
  schedule,
  onEnabled,
  onChange,
}: {
  enabled: boolean
  schedule: UpdateSchedule
  onEnabled: (enabled: boolean) => void
  onChange: (schedule: UpdateSchedule) => void
}) {
  const { t } = useTranslation('compile')
  return (
    <section className="space-y-3 border-t pt-4">
      <label className="flex items-center gap-2 text-sm font-medium">
        <input
          type="checkbox"
          className="size-4 accent-primary"
          checked={enabled}
          onChange={(event) => onEnabled(event.target.checked)}
        />
        {t('schedule.enabled')}
      </label>
      {enabled && (
        <div className="flex flex-wrap items-end gap-4">
          <label className="space-y-1 text-sm">
            <span className="block">{t('schedule.frequency')}</span>
            <select
              className="h-9 rounded-md border bg-background px-3"
              value={schedule.frequency}
              onChange={(event) =>
                onChange(
                  event.target.value === 'weekly'
                    ? {
                        frequency: 'weekly',
                        weekday: 1,
                        hour: schedule.hour,
                        minute: schedule.minute,
                      }
                    : {
                        frequency: 'daily',
                        hour: schedule.hour,
                        minute: schedule.minute,
                      },
                )
              }
            >
              <option value="daily">{t('schedule.daily')}</option>
              <option value="weekly">{t('schedule.weekly')}</option>
            </select>
          </label>
          {schedule.frequency === 'weekly' && (
            <label className="space-y-1 text-sm">
              <span className="block">{t('schedule.weekday')}</span>
              <select
                className="h-9 rounded-md border bg-background px-3"
                value={schedule.weekday}
                onChange={(event) =>
                  onChange({ ...schedule, weekday: Number(event.target.value) })
                }
              >
                {Array.from({ length: 7 }, (_, index) => (
                  <option key={index + 1} value={index + 1}>
                    {t(`schedule.weekdays.${index + 1}`)}
                  </option>
                ))}
              </select>
            </label>
          )}
          <fieldset className="space-y-1 text-sm">
            <legend>{t('schedule.time')}</legend>
            <div className="flex items-center gap-2">
              {timeParts.map((part) => (
                <select
                  key={part}
                  aria-label={t(`schedule.${part}`)}
                  className="h-9 rounded-md border bg-background px-3"
                  value={schedule[part]}
                  onChange={(event) =>
                    onChange({
                      ...schedule,
                      [part]: Number(event.target.value),
                    })
                  }
                >
                  {Array.from(
                    { length: part === 'hour' ? 24 : 60 },
                    (_, value) => (
                      <option key={value} value={value}>
                        {String(value).padStart(2, '0')}
                      </option>
                    ),
                  )}
                </select>
              ))}
            </div>
          </fieldset>
          <p className="pb-2 text-xs text-muted-foreground">
            {t('schedule.timezone')}
          </p>
        </div>
      )}
      <p className="text-xs text-muted-foreground">
        {t(enabled ? 'schedule.saveHint' : 'schedule.manualHint')}
      </p>
    </section>
  )
}
