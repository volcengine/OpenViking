import { ErrorMessage } from '#/shared/components/error-message'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { flushCredentialStorage } from '#/features/connection/lib/credential-storage'
import { StudioSetupRequestError } from '#/features/connection/lib/onboarding'
import { UserKeyError, verifyUserKey } from '#/features/connection/lib/user-key'
import { Button } from '#/shared/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '#/shared/ui/card'
import { Field, FieldLabel } from '#/shared/ui/field'
import { Input } from '#/shared/ui/input'
import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import * as React from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

export function UserKeyPage() {
  const { t } = useTranslation('studio')
  const { connection, switchIdentity, activeProfileId } = useAppConnection()
  const navigate = useNavigate()
  const { returnTo } = useSearch({ from: '/connections/user-key' })
  const [apiKey, setApiKey] = React.useState('')
  const [saving, setSaving] = React.useState(false)
  const [error, setError] = React.useState<string | Error>('')
  const request = React.useRef<AbortController | null>(null)
  React.useEffect(
    () => () => request.current?.abort(),
    [connection, activeProfileId],
  )
  async function save(event: React.FormEvent) {
    event.preventDefault()
    if (saving || !apiKey.trim()) return
    const controller = new AbortController()
    request.current = controller
    setSaving(true)
    setError('')
    let verified
    try {
      verified = await verifyUserKey(connection, apiKey, controller.signal)
      controller.signal.throwIfAborted()
      await switchIdentity({
        accountId: verified.accountId,
        userId: verified.userId,
        apiKey: verified.apiKey,
        preserveProfile: true,
        signal: controller.signal,
      })
    } catch (cause) {
      if (!controller.signal.aborted) {
        setError(
          cause instanceof UserKeyError
            ? t(`userKey.${cause.code}`)
            : cause instanceof StudioSetupRequestError
              ? cause
              : new StudioSetupRequestError('data', cause, {
                  ...connection,
                  apiKey,
                }),
        )
        setSaving(false)
      }
      return
    }
    // A successful identity change remounts content; finish persistence anyway.
    try {
      await flushCredentialStorage()
      toast.success(t('userKey.saved', { user: verified.userId }))
      await navigate({ to: returnTo ?? '/settings' })
    } catch {
      toast.error(t('credentialStorage.saveError'))
    }
  }
  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-5">
      <Link
        to="/settings"
        className="text-sm text-muted-foreground hover:text-foreground"
      >
        {t('profiles.back')}
      </Link>
      <Card>
        <CardHeader>
          <CardTitle>{t('userKey.title')}</CardTitle>
          <CardDescription>{t('userKey.description')}</CardDescription>
        </CardHeader>
        <CardContent>
          <form
            onSubmit={(event) => void save(event)}
            className="flex flex-col gap-5"
          >
            <dl className="grid gap-3 rounded-lg border bg-muted/30 p-4 text-sm">
              <div>
                <dt className="text-muted-foreground">
                  {t('profiles.fields.baseUrl')}
                </dt>
                <dd className="break-all">{connection.baseUrl}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">
                  {t('profiles.fields.accountId')}
                </dt>
                <dd>{connection.accountId}</dd>
              </div>
            </dl>
            <Field>
              <FieldLabel htmlFor="user-api-key">
                {t('profiles.fields.apiKey')}
              </FieldLabel>
              <Input
                id="user-api-key"
                type="password"
                autoComplete="off"
                spellCheck={false}
                autoCapitalize="none"
                value={apiKey}
                disabled={saving}
                onChange={(event) => setApiKey(event.target.value)}
                required
                autoFocus
              />
              <p className="text-sm text-muted-foreground">
                {t('userKey.hint')}
              </p>
            </Field>
            {error ? <ErrorMessage error={error} /> : null}
            <div className="flex justify-end gap-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  request.current?.abort()
                  void navigate({ to: '/settings' })
                }}
              >
                {t('profiles.cancel')}
              </Button>
              <Button type="submit" disabled={saving || !apiKey.trim()}>
                {saving ? t('loading') : t('profiles.saveActivate')}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
