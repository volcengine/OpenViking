import { useState } from 'react'
import { Link } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { useAclManagement } from '#/hooks/use-acl-management'
import { fetchAdminUsers } from '#/lib/admin'
import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import { Field, FieldLabel } from '#/components/ui/field'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '#/components/ui/select'
import { getErrorMessage } from '#/routes/users/-lib/error'

export function ResourceAclIdentityRecovery({
  onRetry,
  error,
}: {
  onRetry?: () => void
  error?: string
} = {}) {
  const { t } = useTranslation('settings')
  const state = useAclManagement(false)
  const [selectedId, setSelectedId] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [switching, setSwitching] = useState(false)
  const users = useQuery({
    queryKey: ['acl-admin-identities', state.identityScopeKey],
    queryFn: () =>
      fetchAdminUsers(state.adminConnection, state.connection.accountId),
    enabled: state.allowed,
    retry: false,
  })
  const admins = users.data?.filter((user) => user.role === 'admin') ?? []
  const effectiveSelectedId = selectedId || admins[0]?.userId || ''
  const selected = admins.find((user) => user.userId === effectiveSelectedId)
  const trusted = state.serverMode === 'trusted'
  const switchSupported = trusted || state.serverMode === 'api_key'
  const needsKey = selected && !trusted && !selected.apiKey

  async function switchUser() {
    if (!selected || !switchSupported) return
    setSwitching(true)
    try {
      await state.switchIdentity({
        accountId: state.connection.accountId,
        userId: selected.userId,
        apiKey: trusted ? '' : selected.apiKey || apiKey.trim(),
        allowLegacyIdentityFallback: true,
      })
      setApiKey('')
      toast.success(t('acl.recovery.switched'))
    } catch (switchError) {
      toast.error(t('acl.recovery.switchFailed'), {
        description: getErrorMessage(switchError),
      })
    } finally {
      setSwitching(false)
    }
  }

  return (
    <section className="mx-auto flex max-w-lg flex-col gap-3 py-8">
      <h2 className="font-medium">{t('acl.recovery.title')}</h2>
      <p className="text-sm text-muted-foreground">
        {t('acl.recovery.description')}
      </p>
      <details className="text-xs text-muted-foreground">
        <summary className="cursor-pointer">
          {t('acl.recovery.scopeTitle')}
        </summary>
        <p className="mt-1">{t('acl.recovery.scope')}</p>
      </details>
      {!switchSupported ? (
        <p className="text-sm">{t('acl.recovery.unsupported')}</p>
      ) : users.isPending ? (
        <p role="status">{t('loading')}</p>
      ) : users.isError ? (
        <>
          <p role="alert">{t('acl.recovery.loadFailed')}</p>
          <details className="text-sm text-muted-foreground">
            <summary>{t('acl.recovery.details')}</summary>
            <p className="mt-2 break-all">{getErrorMessage(users.error)}</p>
          </details>
          <Button variant="outline" onClick={() => void users.refetch()}>
            {t('actions.refresh')}
          </Button>
        </>
      ) : admins.length === 0 ? (
        <p className="text-sm">{t('acl.recovery.noAdmins')}</p>
      ) : (
        <>
          <Field>
            <FieldLabel>{t('acl.recovery.admin')}</FieldLabel>
            <Select
              value={effectiveSelectedId}
              onValueChange={(value) => {
                setSelectedId(value ?? '')
                setApiKey('')
              }}
            >
              <SelectTrigger
                disabled={switching}
                aria-label={t('acl.recovery.admin')}
              >
                <SelectValue placeholder={t('acl.recovery.selectAdmin')}>
                  {effectiveSelectedId || t('acl.recovery.selectAdmin')}
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                {admins.map((admin) => (
                  <SelectItem key={admin.userId} value={admin.userId}>
                    {admin.userId}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          {needsKey && (
            <Field>
              <FieldLabel htmlFor="acl-admin-key">
                {t('acl.recovery.key')}
              </FieldLabel>
              <Input
                id="acl-admin-key"
                type="password"
                autoComplete="off"
                value={apiKey}
                disabled={switching}
                onChange={(event) => setApiKey(event.target.value)}
              />
              <p className="text-sm text-muted-foreground">
                {t('acl.recovery.keyHint')}
              </p>
            </Field>
          )}
          <Button
            disabled={
              !selected || switching || Boolean(needsKey && !apiKey.trim())
            }
            onClick={() => void switchUser()}
          >
            {t(switching ? 'acl.recovery.switching' : 'acl.recovery.switch')}
          </Button>
        </>
      )}
      {admins.length === 0 && (
        <Link to="/users" className="text-sm underline">
          {t('acl.recovery.users')}
        </Link>
      )}
      {onRetry && (
        <Button variant="outline" onClick={onRetry}>
          {t('acl.recovery.retryAccess')}
        </Button>
      )}
      {error && (
        <details className="text-sm text-muted-foreground">
          <summary className="cursor-pointer">
            {t('acl.recovery.details')}
          </summary>
          <p className="mt-2 break-all">{error}</p>
        </details>
      )}
    </section>
  )
}
