import * as React from 'react'
import { CalendarRangeIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '#/components/ui/button'
import { ButtonGroup } from '#/components/ui/button-group'
import { Input } from '#/components/ui/input'
import {
  Popover,
  PopoverContent,
  PopoverDescription,
  PopoverHeader,
  PopoverTitle,
  PopoverTrigger,
} from '#/components/ui/popover'

import { buildCustomTimeRange } from '../-lib/experience'
import type { TimeRange, TimeRangePreset } from '../-lib/types'

const QUICK_PRESETS: readonly TimeRangePreset[] = ['all', '7d', '30d']

const PRESET_LABEL_KEYS: Record<TimeRangePreset, string> = {
  all: 'detail.rangeAll',
  '7d': 'detail.range7d',
  '30d': 'detail.range30d',
  custom: 'detail.rangeCustom',
}

/**
 * Shared time-range control for the impact panel: quick presets plus a
 * custom UTC date range popover. The effective range is reported upward via
 * `onChange` and applied to both the outcome and trajectory queries.
 */
export function TimeRangePicker({
  onChange,
  preset,
  range,
}: {
  onChange: (preset: TimeRangePreset, range: TimeRange) => void
  preset: TimeRangePreset
  range: TimeRange
}) {
  const { t } = useTranslation('agentExperiencePage')
  const [open, setOpen] = React.useState(false)
  const [customStart, setCustomStart] = React.useState(
    range.preset === 'custom' ? (range.startDate ?? '') : '',
  )
  const [customEnd, setCustomEnd] = React.useState(
    range.preset === 'custom' ? (range.endDate ?? '') : '',
  )

  const customError = React.useMemo(() => {
    if (!customStart.trim() && !customEnd.trim()) return undefined
    return buildCustomTimeRange(customStart, customEnd).error
  }, [customEnd, customStart])

  const applyCustom = () => {
    const { error, range: customRange } = buildCustomTimeRange(
      customStart,
      customEnd,
    )
    if (error || !customRange) return
    onChange('custom', customRange)
    setOpen(false)
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <ButtonGroup>
        {QUICK_PRESETS.map((item) => (
          <Button
            key={item}
            type="button"
            size="xs"
            aria-pressed={preset === item}
            variant={preset === item ? 'secondary' : 'ghost'}
            onClick={() => onChange(item, { preset: item })}
          >
            {t(PRESET_LABEL_KEYS[item])}
          </Button>
        ))}
        <Popover onOpenChange={setOpen} open={open}>
          <PopoverTrigger
            className="inline-flex h-6 items-center gap-1 rounded-[min(var(--radius-md),8px)] px-2 text-xs font-medium transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/50 aria-pressed:bg-secondary"
            aria-pressed={preset === 'custom'}
          >
            <CalendarRangeIcon className="size-3" />
            {preset === 'custom' && (range.startDate || range.endDate)
              ? `${range.startDate ?? '…'} ~ ${range.endDate ?? '…'}`
              : t('detail.rangeCustom')}
          </PopoverTrigger>
          <PopoverContent align="end" className="w-72">
            <PopoverHeader>
              <PopoverTitle>{t('detail.rangeCustom')}</PopoverTitle>
              <PopoverDescription>
                {t('detail.rangeUtcHint')}
              </PopoverDescription>
            </PopoverHeader>
            <div className="grid gap-3 px-1 pb-1">
              <label className="grid gap-1.5 text-xs text-muted-foreground">
                {t('detail.rangeStart')}
                <Input
                  aria-label={t('detail.rangeStart')}
                  className="h-8 font-mono text-xs"
                  placeholder="2026-08-01"
                  value={customStart}
                  onChange={(event) => setCustomStart(event.target.value)}
                />
              </label>
              <label className="grid gap-1.5 text-xs text-muted-foreground">
                {t('detail.rangeEnd')}
                <Input
                  aria-label={t('detail.rangeEnd')}
                  className="h-8 font-mono text-xs"
                  placeholder="2026-08-15"
                  value={customEnd}
                  onChange={(event) => setCustomEnd(event.target.value)}
                />
              </label>
              {customError ? (
                <p className="text-xs text-destructive">
                  {customError === 'order'
                    ? t('detail.rangeOrderError')
                    : t('detail.rangeInvalidError')}
                </p>
              ) : null}
              <div className="flex justify-end gap-2">
                <Button
                  type="button"
                  size="xs"
                  variant="ghost"
                  onClick={() => setOpen(false)}
                >
                  {t('detail.rangeCancel')}
                </Button>
                <Button
                  type="button"
                  size="xs"
                  disabled={
                    Boolean(customError) ||
                    (!customStart.trim() && !customEnd.trim())
                  }
                  onClick={applyCustom}
                >
                  {t('detail.rangeApply')}
                </Button>
              </div>
            </div>
          </PopoverContent>
        </Popover>
      </ButtonGroup>
    </div>
  )
}
