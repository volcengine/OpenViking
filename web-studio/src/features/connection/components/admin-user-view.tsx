import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { createConnectionHealthHeaders } from '#/features/connection/lib/auth'
import { connectionChangedError } from '#/features/connection/lib/connection-runtime'
import { flushCredentialStorage } from '#/features/connection/lib/credential-storage'
import { fetchServerHealth } from '#/features/connection/lib/health'
import { createRandomUuid } from '#/shared/lib/browser-crypto'
import { Button } from '#/shared/ui/button'
import { Input } from '#/shared/ui/input'
import { useNavigate } from '@tanstack/react-router'
import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

export function AdminUserView() {
  const { t } = useTranslation('studio')
  const {
    connection,
    serverHealth,
    serverMode,
    saveProfile,
    activateProfile,
    identityScopeKey,
  } = useAppConnection()
  const navigate = useNavigate()
  const [target, setTarget] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const scope = useRef(identityScopeKey)
  scope.current = identityScopeKey
  const capabilities = serverHealth?.capabilities as
    | Record<string, unknown>
    | undefined
  const supported =
    serverMode === 'api_key' &&
    capabilities?.admin_user_delegation === true &&
    (serverHealth?.role === 'admin' ||
      typeof serverHealth?.delegated_by_user_id === 'string')
  if (!supported) return null

  async function open() {
    const source = identityScopeKey
    setPending(true)
    setError('')
    const next = {
      ...connection,
      userId: target.trim(),
      apiKey: '',
      adminApiKey: connection.adminApiKey || connection.apiKey,
      adminDelegation: true,
    }
    try {
      await fetchServerHealth(
        next.baseUrl,
        createConnectionHealthHeaders(next, 'data', 'api_key'),
      )
      if (scope.current !== source) throw connectionChangedError()
      const id = createRandomUuid()
      saveProfile({
        id,
        name: t('delegation.profile', { user: next.userId }),
        connection: next,
      })
      await flushCredentialStorage()
      if (scope.current !== source) throw connectionChangedError()
      activateProfile(id)
      await navigate({ to: '/home' })
    } catch (failure) {
      setError(
        failure instanceof Error ? failure.message : t('delegation.unverified'),
      )
    } finally {
      setPending(false)
    }
  }

  return (
    <section
      className="rounded-lg border p-4"
      aria-label={t('delegation.title')}
    >
      <h2 className="font-semibold">{t('delegation.title')}</h2>
      <p className="my-2 text-sm text-muted-foreground">
        {t('delegation.description', { account: connection.accountId })}
      </p>
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault()
          void open()
        }}
      >
        <label className="flex min-w-48 flex-1 flex-col gap-1 text-sm">
          {t('delegation.user')}
          <Input
            value={target}
            onChange={(event) => setTarget(event.target.value)}
            disabled={pending}
            required
            autoComplete="off"
          />
        </label>
        <Button type="submit" disabled={pending || !target.trim()}>
          {t(pending ? 'delegation.checking' : 'delegation.open')}
        </Button>
      </form>
      {error && (
        <p role="alert" className="mt-2 text-sm text-destructive">
          {error}
        </p>
      )}
    </section>
  )
}
