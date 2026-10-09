import * as React from 'react'

import { Input } from '#/components/ui/input'
import { cn } from '#/lib/utils'

type NumberInputProps = Omit<
  React.ComponentProps<typeof Input>,
  'value' | 'onChange' | 'type'
> & {
  /** `null` or `NaN` shows an empty field. */
  value: number | null
  /** Receives `NaN` while the field is empty. */
  onChange: (value: number) => void
}

function display(value: number | null): string {
  return value === null || Number.isNaN(value) ? '' : String(value)
}

/**
 * Numeric input that keeps what the user typed ("0.", "") while reporting a
 * number upward, so validation can flag empty fields instead of resetting them.
 */
export function NumberInput({
  value,
  onChange,
  className,
  ...props
}: NumberInputProps) {
  const [text, setText] = React.useState(() => display(value))
  const parsed = text.trim() ? Number(text) : Number.NaN
  const matches =
    Object.is(parsed, value) || (value === null && Number.isNaN(parsed))
  if (!matches && text !== display(value)) {
    // The value changed from outside (load, reset): show it.
    setText(display(value))
  }

  return (
    <Input
      {...props}
      type="number"
      inputMode="decimal"
      value={text}
      className={cn('tabular-nums', className)}
      onChange={(event) => {
        setText(event.target.value)
        const next = event.target.value.trim()
        onChange(next ? Number(next) : Number.NaN)
      }}
    />
  )
}
