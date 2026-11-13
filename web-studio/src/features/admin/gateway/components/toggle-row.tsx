import { DocsLink } from '#/features/admin/gateway/components/docs-link'
import { useReadOnly } from '#/features/admin/gateway/components/read-only'
import type { DocsSection } from '#/features/admin/gateway/lib/client-guides'
import type * as React from 'react'

import { Label } from '#/shared/ui/label'
import { Switch } from '#/shared/ui/switch'

type ToggleRowProps = {
  id: string
  label: string
  description: string
  checked: boolean
  onCheckedChange: (checked: boolean) => void
  /** Shown after the label, such as an "Experimental" mark. */
  badge?: React.ReactNode
  /** Docs section linked after the description. */
  docs?: DocsSection
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
  docs,
  invalid = false,
  children,
}: ToggleRowProps) {
  const readOnly = useReadOnly()
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
            {docs ? (
              <>
                {' '}
                <DocsLink section={docs} variant="text" />
              </>
            ) : null}
          </p>
        </div>
        <Switch
          disabled={readOnly}
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
