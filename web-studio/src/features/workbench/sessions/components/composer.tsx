import { SquareIcon } from 'lucide-react'
import { VikingBotComposer } from '@openviking/react/components/viking-bot'
import '@openviking/react/components/viking-bot/style.css'
import { useCallback, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

interface ComposerProps {
  onSend: (message: string) => void | boolean | Promise<void | boolean>
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
  const [isSubmitting, setIsSubmitting] = useState(false)
  const submittingRef = useRef(false)
  const handleSend = useCallback(async () => {
    const trimmed = value.trim()
    if (!trimmed || submittingRef.current) return
    submittingRef.current = true
    setIsSubmitting(true)
    try {
      const accepted = await onSend(trimmed)
      if (accepted === false) return
      // Do not erase text edited while the submission was pending.
      setValue((current) => (current === value ? '' : current))
    } catch {
      // The caller owns error reporting; keep the input available for retry.
    } finally {
      submittingRef.current = false
      setIsSubmitting(false)
    }
  }, [value, onSend])

  return (
    <div className="ov-viking-bot" style={{ height: 'auto', minHeight: 0 }}>
      <VikingBotComposer
        value={value}
        variant={variant}
        disabled={isSubmitting}
        actions={
          isStreaming ? (
            <button
              type="button"
              aria-label={t('chat.cancel')}
              onClick={onCancel}
            >
              <SquareIcon className="size-4" />
            </button>
          ) : undefined
        }
        onValueChange={setValue}
        onSend={() => {
          void handleSend()
        }}
        onCancel={onCancel}
        labels={{
          placeholder: t('chat.placeholder'),
          send: t('chat.send'),
          stop: t('chat.cancel'),
        }}
      />
    </div>
  )
}
