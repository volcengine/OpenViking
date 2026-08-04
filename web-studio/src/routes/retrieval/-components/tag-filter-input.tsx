import { useState } from 'react'

import { Input } from '#/components/ui/input'

const TAG_PATTERN = /^[^=,\s]+=[^=,\s]+$/

export function parseTagFilterInput(input: string): {
  tags: string[]
  valid: boolean
} {
  const tags = input
    .split(',')
    .map((tag) => tag.trim())
    .filter(Boolean)

  return {
    tags: Array.from(new Set(tags)),
    valid: tags.every((tag) => TAG_PATTERN.test(tag)),
  }
}

export function TagFilterInput({
  ariaLabel,
  invalidMessage,
  onChange,
  placeholder,
  value,
}: {
  ariaLabel: string
  invalidMessage: string
  onChange: (tags: string[]) => void
  placeholder: string
  value: string[]
}) {
  const serializedValue = value.join(', ')
  const [draft, setDraft] = useState(serializedValue)
  const [lastValue, setLastValue] = useState(serializedValue)
  const [invalid, setInvalid] = useState(false)

  if (lastValue !== serializedValue) {
    setLastValue(serializedValue)
    setDraft(serializedValue)
    setInvalid(false)
  }

  return (
    <div className="space-y-1">
      <Input
        aria-invalid={invalid}
        aria-label={ariaLabel}
        className="h-8 font-mono text-xs"
        onChange={(event) => {
          const nextDraft = event.target.value
          const parsed = parseTagFilterInput(nextDraft)
          setDraft(nextDraft)
          setInvalid(!parsed.valid)
          if (parsed.valid) {
            onChange(parsed.tags)
          }
        }}
        placeholder={placeholder}
        value={draft}
      />
      {invalid ? (
        <p className="text-xs text-destructive" role="alert">
          {invalidMessage}
        </p>
      ) : null}
    </div>
  )
}
