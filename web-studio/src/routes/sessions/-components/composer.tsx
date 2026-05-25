import { useCallback, useRef, useState } from 'react'
import { ArrowUpIcon, SquareIcon } from 'lucide-react'

import { cn } from '#/lib/utils'

interface ComposerProps {
  onSend: (message: string) => void
  onCancel: () => void
  isStreaming: boolean
}

export function Composer({ onSend, onCancel, isStreaming }: ComposerProps) {
  const [value, setValue] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const resize = useCallback(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`
  }, [])

  const handleSend = useCallback(() => {
    const trimmed = value.trim()
    if (!trimmed) return
    onSend(trimmed)
    setValue('')
    requestAnimationFrame(() => {
      const el = textareaRef.current
      if (el) el.style.height = 'auto'
    })
  }, [value, onSend])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      const isComposing = e.nativeEvent.isComposing || e.keyCode === 229
      if (isComposing) return

      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        if (!isStreaming) handleSend()
      }
    },
    [isStreaming, handleSend],
  )

  return (
    <div className="px-4 pb-4 pt-2">
      <div
        className={cn(
          'mx-auto w-full max-w-3xl rounded-2xl border border-border/50 bg-background/95',
          'shadow-lg shadow-black/8 dark:shadow-black/25',
        )}
      >
        {/* Attachment preview */}
        <textarea
          ref={textareaRef}
          autoFocus
          placeholder="回复..."
          rows={1}
          value={value}
          onChange={(e) => {
            setValue(e.target.value)
            resize()
          }}
          onKeyDown={handleKeyDown}
          className={cn(
            'min-h-[44px] w-full resize-none bg-transparent px-4 pt-3 pb-1 text-sm',
            'placeholder:text-muted-foreground/60',
            'focus-visible:outline-none',
          )}
        />
        <div className="flex items-center justify-end px-3 pb-2.5">
          {isStreaming ? (
            <button
              type="button"
              onClick={onCancel}
              className={cn(
                'inline-flex size-8 items-center justify-center rounded-lg',
                'bg-destructive text-destructive-foreground',
                'transition-colors hover:bg-destructive/90',
              )}
            >
              <SquareIcon className="size-3.5" />
            </button>
          ) : (
            <button
              type="button"
              onClick={handleSend}
              disabled={!value.trim()}
              className={cn(
                'inline-flex size-8 items-center justify-center rounded-lg',
                'bg-primary text-primary-foreground',
                'transition-colors hover:bg-primary/90 disabled:opacity-30',
              )}
            >
              <ArrowUpIcon className="size-3.5" />
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
