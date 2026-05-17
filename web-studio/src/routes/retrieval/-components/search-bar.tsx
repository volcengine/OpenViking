import { SendIcon } from 'lucide-react'
import type { KeyboardEvent, RefObject } from 'react'

import { Button } from '#/components/ui/button'

export function RetrievalSearchBar({
  inputRef,
  onChange,
  onSubmit,
  placeholder,
  query,
}: {
  inputRef: RefObject<HTMLInputElement | null>
  onChange: (value: string) => void
  onSubmit: () => void
  placeholder: string
  query: string
}) {
  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter' && !event.nativeEvent.isComposing) {
      event.preventDefault()
      onSubmit()
    }
  }

  return (
    <div className="flex items-center gap-2 rounded-lg border bg-card px-4 py-3">
      <input
        ref={inputRef}
        type="text"
        value={query}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground/60"
      />
      <Button
        variant="ghost"
        size="icon"
        className="size-8 shrink-0"
        onClick={onSubmit}
        disabled={query.trim().length === 0}
      >
        <SendIcon className="size-4" />
      </Button>
    </div>
  )
}
