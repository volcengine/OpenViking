import { CalendarRange, ChevronDown } from 'lucide-react'
import type { TFunction } from 'i18next'
import { useState } from 'react'

import { Button } from '#/components/ui/button'
import { ButtonGroup } from '#/components/ui/button-group'
import { Field, FieldGroup, FieldLabel } from '#/components/ui/field'
import { Input } from '#/components/ui/input'
import {
  Popover,
  PopoverContent,
  PopoverHeader,
  PopoverTitle,
  PopoverTrigger,
} from '#/components/ui/popover'

const QUICK_RANGES = [
  { labelKey: 'advanced.last24Hours', value: '24h' },
  { labelKey: 'advanced.last7Days', value: '7d' },
  { labelKey: 'advanced.last30Days', value: '30d' },
] as const

const DATE_VALUE_PATTERN = /^\d{4}-\d{2}-\d{2}$/

export function DateRangePicker({
  onChange,
  since,
  t,
  until,
}: {
  onChange: (range: { since?: string; until?: string }) => void
  since?: string
  t: TFunction<'retrieval'>
  until?: string
}) {
  const [open, setOpen] = useState(false)
  const quickRange = QUICK_RANGES.find(({ value }) => value === since && !until)
  const rangeLabel = quickRange
    ? t(quickRange.labelKey)
    : since || until
      ? `${since || '…'} → ${until || '…'}`
      : t('advanced.anyTime')

  return (
    <Popover onOpenChange={setOpen} open={open}>
      <PopoverTrigger className="flex h-8 w-full items-center justify-between gap-2 rounded-md border bg-background px-2.5 text-left text-xs font-normal shadow-xs outline-none transition-colors hover:bg-muted focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50">
        <span className="flex min-w-0 items-center gap-2">
          <CalendarRange className="size-3.5 shrink-0 text-muted-foreground" />
          <span className="truncate">{rangeLabel}</span>
        </span>
        <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" />
      </PopoverTrigger>

      <PopoverContent align="start" className="w-80">
        <PopoverHeader>
          <PopoverTitle>{t('advanced.timeRange')}</PopoverTitle>
        </PopoverHeader>

        <FieldGroup className="gap-4">
          <Field className="gap-2">
            <FieldLabel className="text-xs text-muted-foreground">
              {t('advanced.quickRange')}
            </FieldLabel>
            <ButtonGroup className="w-full">
              {QUICK_RANGES.map(({ labelKey, value }) => (
                <Button
                  className="flex-1"
                  key={value}
                  onClick={() => {
                    onChange({ since: value, until: undefined })
                    setOpen(false)
                  }}
                  size="sm"
                  type="button"
                  variant={
                    quickRange?.value === value ? 'secondary' : 'outline'
                  }
                >
                  {t(labelKey)}
                </Button>
              ))}
            </ButtonGroup>
          </Field>

          <Field className="gap-2">
            <FieldLabel className="text-xs text-muted-foreground">
              {t('advanced.customRange')}
            </FieldLabel>
            <div className="grid grid-cols-2 gap-2">
              <Input
                aria-label={t('advanced.startDate')}
                className="h-8 text-xs"
                onChange={(event) =>
                  onChange({
                    since: event.target.value || undefined,
                    until,
                  })
                }
                type="date"
                value={since && DATE_VALUE_PATTERN.test(since) ? since : ''}
              />
              <Input
                aria-label={t('advanced.endDate')}
                className="h-8 text-xs"
                onChange={(event) =>
                  onChange({
                    since,
                    until: event.target.value || undefined,
                  })
                }
                type="date"
                value={until && DATE_VALUE_PATTERN.test(until) ? until : ''}
              />
            </div>
          </Field>
        </FieldGroup>

        {since || until ? (
          <Button
            className="self-end"
            onClick={() => {
              onChange({ since: undefined, until: undefined })
              setOpen(false)
            }}
            size="sm"
            type="button"
            variant="ghost"
          >
            {t('advanced.clearTimeRange')}
          </Button>
        ) : null}
      </PopoverContent>
    </Popover>
  )
}
