import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import { rotateCredentials } from '../../-api'
import type { Connection } from '../../-api'

export function ConnectionCredentials({
  connection,
  onSaved,
}: {
  connection: Connection
  onSaved: () => void
}) {
  const { t } = useTranslation('vikingbot')
  const [secret, setSecret] = useState('')
  const mutation = useMutation({
    mutationFn: () =>
      rotateCredentials(connection, secret, connection.identity_user),
    onSuccess: () => {
      setSecret('')
      onSaved()
    },
  })
  return (
    <details className="border-t pt-3">
      <summary className="cursor-pointer text-sm">{t('rotate')}</summary>
      <div className="mt-3 max-w-lg space-y-3">
        <p className="text-xs text-muted-foreground">{t('rotateHint')}</p>
        <label className="block space-y-1 text-sm">
          <span>{t('appSecret')}</span>
          <Input
            autoComplete="new-password"
            type="password"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
          />
        </label>
        <p className="text-sm text-muted-foreground">
          {t('runtimeUser')}: {connection.identity_user}
        </p>
        <Button
          size="sm"
          disabled={mutation.isPending}
          onClick={() => mutation.mutate()}
        >
          {t('saveCredentials')}
        </Button>
        {mutation.error && (
          <p role="alert" className="text-sm text-destructive">
            {t('operationFailed')} {mutation.error.message}
          </p>
        )}
        {mutation.isSuccess && (
          <p role="status" className="text-sm">
            {t('credentialsSaved')}
          </p>
        )}
      </div>
    </details>
  )
}
