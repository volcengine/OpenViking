import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { CheckCircle2Icon } from 'lucide-react'
import { Button } from '#/components/ui/button'
import { copyTextToClipboard } from '#/lib/clipboard'
import { verifyConnection } from '../../-api'
import type { Connection } from '../../-api'

const ACTIVITY_FIELDS = ['last_received', 'last_sent'] as const
const VERIFICATION_STAGES = ['received', 'sent'] as const

export function GroupSetup({
  connection,
  onChange,
  onClose,
  manual = false,
}: {
  manual?: boolean
  connection: Connection
  onChange: (connection: Connection) => void
  onClose: () => void
}) {
  const { t } = useTranslation('vikingbot')
  const [copied, setCopied] = useState(false)
  const [copyError, setCopyError] = useState(false)
  const mutation = useMutation({
    mutationFn: () => verifyConnection(connection),
    onSuccess: onChange,
  })
  const verification = connection.status.verification

  return (
    <div className="space-y-4">
      <CheckCircle2Icon className="size-9 text-green-600" />
      <h3 className="font-semibold">
        {t(manual ? 'manualConnected' : 'setupComplete')}
      </h3>
      {manual && (
        <p className="text-sm text-muted-foreground">
          {t('manualConnectedHint')}
        </p>
      )}
      <p className="text-sm">
        {t('connectedAs', {
          name: connection.bot_name,
          user: connection.identity_user,
        })}
      </p>
      <p className="text-sm leading-7 text-muted-foreground">
        {t('startUsingHint')}
      </p>
      <p className="text-xs text-muted-foreground">{t('qr.visibilityHint')}</p>
      {!connection.enabled && <p role="alert">{t('qr.resumeFirst')}</p>}
      <dl className="grid gap-4 rounded-lg bg-muted/50 p-4 sm:grid-cols-2">
        {ACTIVITY_FIELDS.map((key) => (
          <div key={key} className="space-y-1">
            <dt className="text-xs text-muted-foreground">
              {t(key === 'last_received' ? 'lastReceived' : 'sent')}
            </dt>
            <dd className="text-sm">
              {connection.status[key]
                ? new Date(connection.status[key]).toLocaleString()
                : t('noActivity')}
            </dd>
          </div>
        ))}
      </dl>
      <Button onClick={onClose}>{t('finish')}</Button>
      <details className="rounded-lg border p-4">
        <summary className="cursor-pointer text-sm font-medium">
          {t('connectionHelp')}
        </summary>
        <div className="mt-4 space-y-4">
          <p className="text-sm text-muted-foreground">{t('troubleshoot')}</p>
          <p className="text-sm text-muted-foreground">{t('groupHint')}</p>
          <Button
            variant="outline"
            disabled={mutation.isPending || !connection.enabled}
            onClick={() => mutation.mutate()}
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
              {t('operationFailed')} {mutation.error.message}
            </p>
          )}
        </div>
      </details>
    </div>
  )
}
