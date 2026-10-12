import type * as React from 'react'
import { InfoIcon } from 'lucide-react'

import { Card, CardContent } from '#/components/ui/card'
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '#/components/ui/tooltip'
import { cn } from '#/lib/utils'

type MetricCardProps = {
  icon: React.ReactNode
  label: string
  value: React.ReactNode
  /** Small line under the value, e.g. "142 new messages". */
  footnote?: React.ReactNode
  /** Explanation shown in a tooltip next to the label. */
  hint?: string
  /** Highlights the card as the headline metric. */
  emphasis?: boolean
  /** Colors the value when it needs attention. */
  tone?: 'default' | 'warning' | 'danger'
}

const toneClass = {
  default: '',
  warning: 'text-amber-600 dark:text-amber-300',
  danger: 'text-destructive',
}

/** KPI tile: label with optional hint, large value, footnote and icon. */
export function MetricCard({
  icon,
  label,
  value,
  footnote,
  hint,
  emphasis = false,
  tone = 'default',
}: MetricCardProps) {
  return (
    <Card
      className={cn(
        'bg-card/70 py-0',
        emphasis && 'bg-primary/[0.025] ring-primary/20',
      )}
    >
      <CardContent className="flex min-h-24 items-start justify-between gap-4 px-5 py-4">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
            <span className="truncate">{label}</span>
            {hint ? (
              <Tooltip>
                <TooltipTrigger
                  render={
                    <button
                      type="button"
                      aria-label={hint}
                      className="inline-flex rounded-sm text-muted-foreground/70 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                    />
                  }
                >
                  <InfoIcon className="size-3.5" />
                </TooltipTrigger>
                <TooltipContent className="max-w-72 text-pretty">
                  {hint}
                </TooltipContent>
              </Tooltip>
            ) : null}
          </div>
          <p
            className={cn(
              'mt-1 text-2xl font-semibold tabular-nums',
              toneClass[tone],
            )}
          >
            {value}
          </p>
          {footnote ? (
            <p className="mt-1 text-xs text-muted-foreground">{footnote}</p>
          ) : null}
        </div>
        <div
          className={cn(
            'flex size-9 shrink-0 items-center justify-center rounded-md border bg-background/70 text-muted-foreground',
            emphasis && 'border-primary/20 text-primary',
          )}
        >
          {icon}
        </div>
      </CardContent>
    </Card>
  )
}
