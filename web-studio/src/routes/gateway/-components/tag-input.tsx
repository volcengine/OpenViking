import * as React from 'react'
import { PlusIcon, XIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Badge } from '#/components/ui/badge'
import { PLAIN_INPUT_PROPS } from '#/lib/form-input'
import { cn } from '#/lib/utils'

type TagInputProps = {
  value: string[]
  onChange: (value: string[]) => void
  /** Values offered as one-click chips below the input. */
  suggestions?: string[]
  placeholder?: string
  id?: string
  'aria-label'?: string
  'aria-invalid'?: boolean
  disabled?: boolean
}

const SUGGESTION_LIMIT = 12

/** Splits pasted or typed text into list entries (commas and new lines). */
export function splitTags(text: string): string[] {
  return text
    .split(/[,\n]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

/**
 * Chips for a list of strings: Enter or comma adds, Backspace on an empty
 * input removes the last chip, pasted lists are split.
 */
export function TagInput({
  value,
  onChange,
  suggestions = [],
  placeholder,
  id,
  disabled,
  ...aria
}: TagInputProps) {
  const { t } = useTranslation('gateway')
  const [text, setText] = React.useState('')

  function add(items: string[]) {
    const next = [...value]
    for (const item of items) if (!next.includes(item)) next.push(item)
    if (next.length !== value.length) onChange(next)
    setText('')
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Enter' || event.key === ',') {
      event.preventDefault()
      add(splitTags(text))
    } else if (event.key === 'Backspace' && !text && value.length > 0) {
      onChange(value.slice(0, -1))
    }
  }

  function onPaste(event: React.ClipboardEvent<HTMLInputElement>) {
    const pasted = event.clipboardData.getData('text')
    if (!/[,\n]/.test(pasted)) return
    event.preventDefault()
    add(splitTags(text + pasted))
  }

  const query = text.trim().toLowerCase()
  const offered = suggestions
    .filter((item) => !value.includes(item))
    .filter((item) => item.toLowerCase().includes(query))
    .slice(0, SUGGESTION_LIMIT)

  return (
    <div className="grid gap-2">
      <div
        className={cn(
          'flex min-h-9 w-full flex-wrap items-center gap-1.5 rounded-md border border-input bg-transparent px-2 py-1.5 shadow-xs transition-[color,box-shadow] focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/50 dark:bg-input/30',
          aria['aria-invalid'] &&
            'border-destructive ring-3 ring-destructive/20 dark:border-destructive/50',
          disabled && 'pointer-events-none opacity-50',
        )}
      >
        {value.map((item) => (
          <Badge
            key={item}
            variant="secondary"
            className="h-6 max-w-full gap-1 pr-1 font-mono font-normal"
          >
            <span className="truncate">{item}</span>
            <button
              type="button"
              aria-label={t('tags.remove', { value: item })}
              className="rounded-sm text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
              onClick={() => onChange(value.filter((other) => other !== item))}
            >
              <XIcon className="size-3" />
            </button>
          </Badge>
        ))}
        <input
          {...PLAIN_INPUT_PROPS}
          {...aria}
          id={id}
          value={text}
          disabled={disabled}
          placeholder={value.length ? undefined : placeholder}
          className="h-6 min-w-24 flex-1 bg-transparent font-mono text-sm outline-none placeholder:font-sans placeholder:text-muted-foreground"
          onChange={(event) => setText(event.target.value)}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          onBlur={() => add(splitTags(text))}
        />
      </div>
      {offered.length > 0 && !disabled ? (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-muted-foreground">
            {t('tags.suggestions')}
          </span>
          {offered.map((item) => (
            <button
              key={item}
              type="button"
              className="inline-flex h-6 items-center gap-1 rounded-full border border-dashed px-2 font-mono text-xs text-muted-foreground transition-colors hover:border-solid hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
              onClick={() => add([item])}
            >
              <PlusIcon className="size-3" />
              {item}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  )
}
