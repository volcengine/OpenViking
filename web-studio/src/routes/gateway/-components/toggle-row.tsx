import type * as React from 'react'

import { Label } from '#/components/ui/label'
import { Switch } from '#/components/ui/switch'

type ToggleRowProps = {
  id: string
  label: string
  description: string
  checked: boolean
  onCheckedChange: (checked: boolean) => void
  /** Shown after the label, such as an "Experimental" mark. */
  badge?: React.ReactNode
  /** A dependent setting is invalid, so they stay visible while the switch is off. */
  invalid?: boolean
  /** Dependent settings, shown below the row while it is on. */
  children?: React.ReactNode
}

/** Bordered row with a label, a one-line description and a switch. */
export function ToggleRow({
  id,
  label,
  description,
  checked,
  onCheckedChange,
  badge,
  invalid = false,
  children,
}: ToggleRowProps) {
  return (
    <div className="grid rounded-lg border">
      <div className="flex items-start justify-between gap-4 px-4 py-3">
        <div className="grid gap-1">
          <span className="flex flex-wrap items-center gap-2">
            <Label htmlFor={id}>{label}</Label>
            {badge}
          </span>
          <p className="text-xs leading-5 text-muted-foreground">
            {description}
          </p>
        </div>
        <Switch
          id={id}
          checked={checked}
          aria-label={label}
          onCheckedChange={(value) => onCheckedChange(value)}
        />
      </div>
      {(checked || invalid) && children ? (
        <div className="grid gap-4 border-t p-4">{children}</div>
      ) : null}
    </div>
  )
}
