import { fetchServerHealth } from '#/features/connection/lib/health'
import { useNavigate } from '@tanstack/react-router'
import * as React from 'react'
import { useTranslation } from 'react-i18next'

import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { PLAIN_INPUT_PROPS } from '#/shared/lib/form-input'
import { Button } from '#/shared/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '#/shared/ui/card'
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldLabel,
} from '#/shared/ui/field'
import { Input } from '#/shared/ui/input'

/** Whether the gateway reports OpenViking itself unreachable for this key. */
async function openvikingUnreachable(baseUrl: string, apiKey: string) {
  try {
    const response = await fetch(
      `${baseUrl.replace(/\/+$/, '')}/gateway/admin/access`,
      { headers: { 'X-API-Key': apiKey } },
    )
    const body: unknown = await response.json()
    return (
      typeof body === 'object' &&
      body !== null &&
      (body as { reason?: unknown }).reason === 'openviking_unreachable'
    )
  } catch {
    return false
  }
}

export function GatewayConnectionSettings() {
  const { t } = useTranslation('settings')
  const { connection, openvikingUrl, saveConnection, serverMode } =
    useAppConnection()
  const navigate = useNavigate()
  const [apiKey, setApiKey] = React.useState(connection.apiKey)
  const [pending, setPending] = React.useState(false)
  const [error, setError] = React.useState('')
  const misplacedKey = apiKey.startsWith('ovgw_') || apiKey.startsWith('ovgm_')

  async function login(event: React.FormEvent) {
    event.preventDefault()
    if (misplacedKey) return
    setPending(true)
    setError('')
    try {
      const health = await fetchServerHealth(
        connection.baseUrl,
        apiKey.trim() ? { 'X-API-Key': apiKey.trim() } : {},
      )
      // The gateway answers an invalid key with health minus identity, and
      // so it does while OpenViking is down; /access tells the two apart.
      if (apiKey.trim() && typeof health.user_id !== 'string') {
        setError(
          (await openvikingUnreachable(connection.baseUrl, apiKey.trim()))
            ? t('gatewayConnection.unreachable', { url: openvikingUrl })
            : t('gatewayConnection.invalidKey'),
        )
        return
      }
      saveConnection({
        ...connection,
        adminApiKey: '',
        apiKey: apiKey.trim(),
        accountId:
          typeof health.account_id === 'string'
            ? health.account_id
            : connection.accountId,
        userId:
          typeof health.user_id === 'string'
            ? health.user_id
            : connection.userId,
      })
      await navigate({ to: '/admin/gateway' })
    } catch {
      setError(t('gatewayConnection.gatewayUnreachable'))
    } finally {
      setPending(false)
    }
  }

  return (
    <Card className="w-full max-w-xl">
      <CardHeader>
        <CardTitle>{t('gatewayConnection.title')}</CardTitle>
        {openvikingUrl ? (
          <p className="break-all text-sm text-muted-foreground">
            {t('gatewayConnection.openvikingUrl', { url: openvikingUrl })}
          </p>
        ) : null}
      </CardHeader>
      <CardContent>
        <form className="grid gap-4" onSubmit={(event) => void login(event)}>
          <Field>
            <FieldLabel htmlFor="gateway-openviking-key">
              {t('gatewayConnection.apiKey')}
            </FieldLabel>
            <FieldContent>
              <Input
                id="gateway-openviking-key"
                type="password"
                value={apiKey}
                required={serverMode !== 'dev'}
                onChange={(event) => {
                  setApiKey(event.target.value)
                  setError('')
                }}
                {...PLAIN_INPUT_PROPS}
              />
              <FieldDescription>
                {t('gatewayConnection.keyHint')}
              </FieldDescription>
              {misplacedKey || error ? (
                <p role="alert" className="text-sm text-destructive">
                  {misplacedKey ? t('gatewayConnection.wrongKey') : error}
                </p>
              ) : null}
            </FieldContent>
          </Field>
          <Button type="submit" disabled={pending || misplacedKey}>
            {t(
              pending
                ? 'gatewayConnection.connecting'
                : 'gatewayConnection.connect',
            )}
          </Button>
        </form>
      </CardContent>
    </Card>
  )
}
