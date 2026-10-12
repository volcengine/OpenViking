import type * as React from 'react'
import { useTranslation } from 'react-i18next'

import {
  Field,
  FieldDescription,
  FieldError,
  FieldLabel,
} from '#/components/ui/field'
import { cn } from '#/lib/utils'

type SettingFieldProps = {
  label: React.ReactNode
  /** Id of the control, so the label focuses it. */
  htmlFor?: string
  /** One line on what the setting does. */
  description?: React.ReactNode
  /** Unit shown after the control, e.g. "tokens". */
  unit?: React.ReactNode
  /** Formatted default, rendered as "Default: …". */
  defaultValue?: string
  /** Already-translated validation message. */
  error?: string
  className?: string
  /** The control: Input, NumberInput, Select, TagInput… */
  children: React.ReactNode
}

/** Label, control with unit, description, "Default: …" and inline error. */
export function SettingField({
  label,
  htmlFor,
  description,
  unit,
  defaultValue,
  error,
  className,
  children,
}: SettingFieldProps) {
  const { t } = useTranslation('gateway')
  const hasDefault = Boolean(defaultValue)
  return (
    <Field data-invalid={Boolean(error)} className={cn('gap-2', className)}>
      <FieldLabel htmlFor={htmlFor}>{label}</FieldLabel>
      {unit ? (
        <div className="flex items-center gap-2">
          <div className="min-w-0 flex-1">{children}</div>
          <span className="shrink-0 text-sm text-muted-foreground">{unit}</span>
        </div>
      ) : (
        children
      )}
      {description || hasDefault ? (
        <FieldDescription className="text-xs">
          {description}
          {description && hasDefault ? ' ' : null}
          {hasDefault ? (
            <span className="whitespace-nowrap text-muted-foreground/80">
              {t('field.default', { value: defaultValue })}
            </span>
          ) : null}
        </FieldDescription>
      ) : null}
      {error ? <FieldError>{error}</FieldError> : null}
    </Field>
  )
}
