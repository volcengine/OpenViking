import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { CheckCircle2Icon } from 'lucide-react'
import { Button } from '#/components/ui/button'
import { copyTextToClipboard } from '#/lib/clipboard'
import { updateConnection } from '../../-api'
import type { Connection } from '../../-api'

const VERIFICATION_STAGES = ['received', 'sent'] as const

export function GroupSetup({
  connection,
  onChange,
  onClose,
}: {
  connection: Connection
  onChange: (connection: Connection) => void
  onClose: () => void
}) {
  const { t } = useTranslation('vikingbot')
  const [copied, setCopied] = useState(false)
  const [copyError, setCopyError] = useState(false)
  const mutation = useMutation({
    mutationFn: (action: 'verify' | 'step') =>
      updateConnection(connection, action, action === 'step' ? 5 : undefined),
    onSuccess: onChange,
  })
  const verification = connection.status.verification
  if (connection.step === 5)
    return (
      <div className="space-y-4">
        <CheckCircle2Icon className="size-9 text-green-600" />
        <h3 className="font-semibold">{t('done')}</h3>
        <p className="text-sm text-muted-foreground">{t('doneHint')}</p>
        <Button onClick={onClose}>{t('finish')}</Button>
      </div>
    )
  return (
    <div className="space-y-4">
      <h3 className="font-semibold">{t('qr.addGroup')}</h3>
      <p className="text-sm">
        {t('connectedAs', {
          name: connection.bot_name,
          user: connection.identity_user,
        })}
      </p>
      <p className="text-sm leading-7 text-muted-foreground">
        {t('groupHint')}
      </p>
      <p className="text-xs text-muted-foreground">{t('qr.visibilityHint')}</p>
      {!connection.enabled && <p role="alert">{t('qr.resumeFirst')}</p>}
      <Button
        variant="outline"
        disabled={mutation.isPending || !connection.enabled}
        onClick={() => mutation.mutate('verify')}
      >
        {t('test')}
      </Button>
      {verification && (
        <div className="space-y-3 rounded-lg bg-muted p-4">
          <p className="break-words font-mono text-sm">
            {t('testText', { code: verification.code })}
          </p>
          <Button
            size="sm"
            variant="outline"
            onClick={async () => {
              try {
                await copyTextToClipboard(
                  t('testText', { code: verification.code }),
                )
                setCopied(true)
                setCopyError(false)
              } catch {
                setCopyError(true)
              }
            }}
          >
            {t(copied ? 'copied' : 'copy')}
          </Button>
          {copyError && <p role="alert">{t('qr.copyFailed')}</p>}
          {VERIFICATION_STAGES.map((stage) => (
            <p key={stage} role="status" className="text-sm">
              {t(stage)} · {t(verification[stage] ? 'verified' : 'waiting')}
            </p>
          ))}
          {verification.expires_at * 1000 < Date.now() &&
            !verification.sent && <p role="alert">{t('expired')}</p>}
        </div>
      )}
      {mutation.error && (
        <p role="alert" className="text-sm text-destructive">
          {mutation.error.message}
        </p>
      )}
      <Button
        disabled={!verification?.sent || mutation.isPending}
        onClick={() => mutation.mutate('step')}
      >
        {t('visible')}
      </Button>
    </div>
  )
}
