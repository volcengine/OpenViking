import studioI18n from '#/i18n/core'
import { SquareIcon } from 'lucide-react'
import { VikingBotComposer } from '@openviking/react/components/viking-bot'
import '@openviking/react/components/viking-bot/style.css'
import { useCallback, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '#/shared/ui/button'

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
  // Guard the request without toggling Ark's editor editability, which
  // emits a content update and can restore the previous draft.
  const submittingRef = useRef(false)
  // An empty draft uses the shared stop button. With a follow-up draft,
  // keep both stop and send available.
  const showStopAsSubmit = isStreaming && !value.trim()
  const showStopBesideSend = isStreaming && !showStopAsSubmit
  const handleSend = useCallback(async () => {
    const trimmed = value.trim()
    if (!trimmed || submittingRef.current) return
    submittingRef.current = true
    try {
      const accepted = await onSend(trimmed)
      if (accepted === false) return
      // Do not erase text edited while the submission was pending.
      setValue((current) => (current === value ? '' : current))
    } catch {
      // The caller owns error reporting; keep the input available for retry.
    } finally {
      submittingRef.current = false
    }
  }, [value, onSend])

  return (
    <div className="ov-viking-bot" style={{ height: 'auto', minHeight: 0 }}>
      <VikingBotComposer
        locale={studioI18n.language.startsWith('zh') ? 'zh-CN' : 'en-US'}
        value={value}
        variant={variant}
        isSending={showStopAsSubmit}
        className="studio-session-composer"
        actions={
          showStopBesideSend ? (
            <Button
              type="button"
              size="icon-sm"
              variant="secondary"
              className="rounded-full"
              aria-label={t('chat.cancel')}
              onClick={() => onCancel()}
            >
              <SquareIcon className="size-4" />
            </Button>
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
