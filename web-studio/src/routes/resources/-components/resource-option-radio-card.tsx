import type { ReactNode } from 'react'

import { RadioGroupItem } from '#/components/ui/radio-group'
import { cn } from '#/lib/utils'

type ResourceOptionRadioCardProps = {
  description: ReactNode
  disabled?: boolean
  title: ReactNode
  value: string
}

export function ResourceOptionRadioCard({
  description,
  disabled = false,
  title,
  value,
}: ResourceOptionRadioCardProps) {
  return (
    <label
      className={cn(
        'flex cursor-pointer items-start gap-3 rounded-md border border-border/50 p-3',
        disabled && 'cursor-not-allowed opacity-60',
      )}
    >
      <RadioGroupItem value={value} disabled={disabled} className="mt-0.5" />
      <span className="grid gap-1">
        <span className="text-sm font-medium">{title}</span>
        <span className="text-xs text-muted-foreground">{description}</span>
      </span>
    </label>
  )
}
