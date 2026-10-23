import { useCallback, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowUpIcon, ImagePlusIcon, SquareIcon, XIcon } from 'lucide-react'

import type { BotChatImage } from '@ov-server/bot/v1/chat'

import {
  MAX_SESSION_IMAGES,
  MAX_SESSION_IMAGE_BYTES,
  draftImageToPart,
  prepareImageFiles,
} from '#/lib/sessions/image-attachments'
import type {
  DraftImage,
  ImagePrepareFailure,
} from '#/lib/sessions/image-attachments'
import { cn } from '#/lib/utils'

interface ComposerProps {
  onSend: (
    message: string,
    images?: BotChatImage[],
  ) => void | boolean | Promise<void | boolean>
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
  const { t } = useTranslation('sessions')
  const [value, setValue] = useState('')
  const [images, setImages] = useState<DraftImage[]>([])
  const [attachError, setAttachError] = useState<string>()
  const [isDragging, setIsDragging] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const submittingRef = useRef(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const isCompact = variant === 'compact'

  const resize = useCallback(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`
  }, [])

  const describeFailure = useCallback(
    (reason: ImagePrepareFailure) => {
      switch (reason) {
        case 'unsupported':
          return t('chat.imageTypeUnsupported')
        case 'tooLarge':
          return t('chat.imageTooLarge', {
            size: Math.round(MAX_SESSION_IMAGE_BYTES / (1024 * 1024)),
          })
        default:
          return t('chat.imageReadFailed')
      }
    },
    [t],
  )

  const addFiles = useCallback(
    async (files: File[]) => {
      // Steering mid-stream only carries text, so images wait for the next turn.
      if (isStreaming || files.length === 0) return
      setAttachError(undefined)
      const remaining = MAX_SESSION_IMAGES - images.length
      if (remaining <= 0) {
        setAttachError(t('chat.imageLimit', { count: MAX_SESSION_IMAGES }))
        return
      }
      const { images: nextImages, errors } = await prepareImageFiles(
        files,
        remaining,
      )
      if (nextImages.length > 0) {
        setImages((current) =>
          [...current, ...nextImages].slice(0, MAX_SESSION_IMAGES),
        )
      }
      if (errors.length > 0) setAttachError(describeFailure(errors[0]))
      else if (files.length > remaining) {
        setAttachError(t('chat.imageLimit', { count: MAX_SESSION_IMAGES }))
      }
    },
    [describeFailure, images.length, isStreaming, t],
  )

  const removeImage = useCallback((id: string) => {
    setImages((current) => current.filter((image) => image.id !== id))
    setAttachError(undefined)
  }, [])

  const handleSend = useCallback(async () => {
    const trimmed = value.trim()
    if ((!trimmed && images.length === 0) || submittingRef.current) return
    submittingRef.current = true
    setIsSubmitting(true)
    try {
      // Keep the text-only call shape single-argument for existing callers.
      const accepted = images.length
        ? await onSend(trimmed, images.map(draftImageToPart))
        : await onSend(trimmed)
      if (accepted === false) return
      // Do not erase text edited while the submission was pending.
      setValue((current) => (current === value ? '' : current))
      setImages((current) => (current === images ? [] : current))
      setAttachError(undefined)
    } catch {
      // The caller owns error reporting; keep the input available for retry.
    } finally {
      submittingRef.current = false
      setIsSubmitting(false)
    }
  }, [images, onSend, value])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      const isComposing = e.nativeEvent.isComposing || e.keyCode === 229
      if (isComposing) return

      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        void handleSend()
      }
    },
    [handleSend],
  )

  const handlePaste = useCallback(
    (e: React.ClipboardEvent) => {
      const files = Array.from(e.clipboardData.files).filter((file) =>
        file.type.startsWith('image/'),
      )
      if (files.length === 0) return
      e.preventDefault()
      void addFiles(files)
    },
    [addFiles],
  )

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      setIsDragging(false)
      const files = Array.from(e.dataTransfer.files).filter((file) =>
        file.type.startsWith('image/'),
      )
      void addFiles(files)
    },
    [addFiles],
  )

  const canAttach = !isStreaming
  const canSend = Boolean(value.trim()) || images.length > 0

  return (
    <div className={cn(isCompact ? 'px-3 pb-3 pt-2' : 'px-4 pb-4 pt-2')}>
      <div
        onDragOver={(e) => {
          if (!canAttach) return
          e.preventDefault()
          setIsDragging(true)
        }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={handleDrop}
        className={cn(
          'mx-auto w-full border bg-background/95',
          isCompact ? 'max-w-none' : 'max-w-4xl',
          isCompact ? 'rounded-xl' : 'rounded-2xl',
          'shadow-lg shadow-black/8 dark:shadow-black/25',
          isDragging
            ? 'border-primary/60 ring-2 ring-primary/20'
            : 'border-border/50',
        )}
      >
        {images.length > 0 && (
          <div
            className={cn(
              'flex flex-wrap gap-2',
              isCompact ? 'px-3 pt-2.5' : 'px-4 pt-3',
            )}
          >
            {images.map((image) => (
              <div
                key={image.id}
                className="group relative size-16 overflow-hidden rounded-lg border border-border/60 bg-muted"
              >
                <img
                  src={image.dataUrl}
                  alt={image.name}
                  className="size-full object-cover"
                />
                <button
                  type="button"
                  aria-label={t('chat.removeImage')}
                  onClick={() => removeImage(image.id)}
                  className={cn(
                    'absolute right-0.5 top-0.5 inline-flex size-5 items-center justify-center rounded-full',
                    'bg-black/60 text-white opacity-0 transition-opacity',
                    'group-hover:opacity-100 focus-visible:opacity-100 focus-visible:outline-none',
                  )}
                >
                  <XIcon className="size-3" />
                </button>
              </div>
            ))}
          </div>
        )}
        {attachError && (
          <p
            className={cn(
              'text-xs text-destructive',
              isCompact ? 'px-3 pt-2' : 'px-4 pt-2.5',
            )}
          >
            {attachError}
          </p>
        )}
        <textarea
          ref={textareaRef}
          autoFocus
          aria-label={t('chat.placeholder')}
          placeholder={t('chat.placeholder')}
          rows={1}
          value={value}
          onChange={(e) => {
            setValue(e.target.value)
            resize()
          }}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
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
            'flex items-center justify-between gap-2',
            isCompact ? 'px-2.5 pb-2' : 'px-3 pb-2.5',
          )}
        >
          <div className="flex items-center gap-1">
            <input
              ref={fileInputRef}
              type="file"
              accept="image/png,image/jpeg,image/webp,image/gif"
              multiple
              hidden
              onChange={(e) => {
                void addFiles(Array.from(e.target.files ?? []))
                e.target.value = ''
              }}
            />
            <button
              type="button"
              aria-label={t('chat.attachImage')}
              title={t('chat.attachImage')}
              disabled={!canAttach || images.length >= MAX_SESSION_IMAGES}
              onClick={() => fileInputRef.current?.click()}
              className={cn(
                'inline-flex size-8 items-center justify-center rounded-lg',
                'text-muted-foreground transition-colors hover:bg-accent hover:text-foreground',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                'disabled:opacity-30',
              )}
            >
              <ImagePlusIcon className="size-4" />
            </button>
          </div>
          <div className="flex items-center gap-2">
            {isStreaming && (
              <button
                type="button"
                aria-label={t('chat.cancel')}
                onClick={onCancel}
                className={cn(
                  'inline-flex size-8 items-center justify-center rounded-lg',
                  'bg-muted text-foreground',
                  'transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                )}
              >
                <SquareIcon className="size-3.5" />
              </button>
            )}
            {(!isStreaming || canSend) && (
              <button
                type="button"
                aria-label={t('chat.send')}
                onClick={handleSend}
                disabled={!canSend || isSubmitting}
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
    </div>
  )
}
