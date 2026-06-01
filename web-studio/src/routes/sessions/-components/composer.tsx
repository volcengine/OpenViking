import { useCallback, useRef, useState } from 'react'
import { ArrowUpIcon, SquareIcon } from 'lucide-react'

import { cn } from '#/lib/utils'

interface ComposerProps {
  onSend: (message: string) => void
  onCancel: () => void
  isStreaming: boolean
  variant?: 'default' | 'compact'
}

export function Composer({
  onSend,
  onCancel,
  isStreaming,
  variant = 'default',
}: ComposerProps) {
  const [value, setValue] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const isCompact = variant === 'compact'

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
    <div className={cn(isCompact ? 'px-3 pb-3 pt-2' : 'px-4 pb-4 pt-2')}>
      <div
        className={cn(
          'mx-auto w-full max-w-3xl border border-border/50 bg-background/95',
          isCompact ? 'rounded-xl' : 'rounded-2xl',
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
            'w-full resize-none bg-transparent text-sm',
            isCompact
              ? 'min-h-[38px] px-3 pb-0.5 pt-2.5'
              : 'min-h-[44px] px-4 pb-1 pt-3',
            'placeholder:text-muted-foreground/60',
            'focus-visible:outline-none',
          )}
        />
        <div
          className={cn(
            'flex items-center justify-end',
            isCompact ? 'px-2.5 pb-2' : 'px-3 pb-2.5',
          )}
        >
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
