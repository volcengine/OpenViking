import { useCallback, useRef, useState } from 'react'
import { ArrowUpIcon, FileIcon, PaperclipIcon, SquareIcon, XIcon } from 'lucide-react'

import { cn } from '#/lib/utils'
import type { FileAttachment } from '#/routes/sessions/-hooks/use-file-attachment'

interface ComposerProps {
  onSend: (message: string) => void
  onCancel: () => void
  isStreaming: boolean
  attachment?: FileAttachment
  onAttach?: (file: File) => void
  onClearAttachment?: () => void
}

export function Composer({
  onSend,
  onCancel,
  isStreaming,
  attachment,
  onAttach,
  onClearAttachment,
}: ComposerProps) {
  const [value, setValue] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const isUploading = attachment?.phase === 'uploading'
  const hasAttachment = attachment?.phase === 'ready' || isUploading

  const resize = useCallback(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`
  }, [])

  const handleSend = useCallback(() => {
    const trimmed = value.trim()
    if (!trimmed && !attachment?.tempFileId) return
    onSend(trimmed)
    setValue('')
    requestAnimationFrame(() => {
      const el = textareaRef.current
      if (el) el.style.height = 'auto'
    })
  }, [value, onSend, attachment?.tempFileId])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        if (!isStreaming && !isUploading) handleSend()
      }
    },
    [isStreaming, isUploading, handleSend],
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
        {hasAttachment && attachment && (
          <div className="flex items-center gap-2 border-b border-border/40 px-4 py-2 text-xs">
            <FileIcon className="size-3.5 shrink-0 text-muted-foreground" />
            <span className="min-w-0 flex-1 truncate font-medium">
              {attachment.fileName}
            </span>
            {isUploading ? (
              <span className="shrink-0 tabular-nums text-muted-foreground">
                {attachment.progress}%
              </span>
            ) : (
              <span className="shrink-0 text-muted-foreground">
                {formatFileSize(attachment.fileSize)}
              </span>
            )}
            <button
              type="button"
              onClick={onClearAttachment}
              className="shrink-0 rounded p-0.5 text-muted-foreground hover:text-foreground"
            >
              <XIcon className="size-3" />
            </button>
          </div>
        )}
        {attachment?.phase === 'error' && (
          <div className="flex items-center gap-2 border-b border-destructive/20 bg-destructive/5 px-4 py-2 text-xs text-destructive">
            <span className="min-w-0 flex-1 truncate">{attachment.error}</span>
            <button
              type="button"
              onClick={onClearAttachment}
              className="shrink-0 rounded p-0.5 hover:text-destructive/80"
            >
              <XIcon className="size-3" />
            </button>
          </div>
        )}

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
        <div className="flex items-center justify-between px-3 pb-2.5">
          {/* File attach button */}
          <div>
            <input
              ref={fileInputRef}
              type="file"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0]
                if (file && onAttach) onAttach(file)
                e.target.value = ''
              }}
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={isUploading}
              className={cn(
                'inline-flex size-8 items-center justify-center rounded-lg',
                'text-muted-foreground transition-colors',
                'hover:bg-accent hover:text-accent-foreground',
                'disabled:opacity-40',
              )}
            >
              <PaperclipIcon className="size-3.5" />
            </button>
          </div>

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
              disabled={isUploading || (!value.trim() && !attachment?.tempFileId)}
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

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
