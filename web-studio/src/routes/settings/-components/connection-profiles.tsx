import * as React from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronDownIcon } from 'lucide-react'
import { Button } from '#/components/ui/button'
import { Badge } from '#/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '#/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '#/components/ui/dialog'
import { Input } from '#/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '#/components/ui/select'
import { Field, FieldLabel } from '#/components/ui/field'
import { useAppConnection } from '#/hooks/use-app-connection'
import { createRandomUuid } from '#/lib/browser-crypto'
import { PLAIN_INPUT_PROPS } from '#/lib/form-input'
import type { ConnectionProfile } from '#/lib/connection/profiles'
import { formatHostedIdentity } from '#/lib/connection/identity-label'
import {
  connectionProfileFingerprint,
  isValidConnectionUrl,
  normalizeProfileConnection,
} from '#/lib/connection/profiles'

const PROFILE_FIELDS = [
  'baseUrl',
  'apiKey',
  'adminApiKey',
  'accountId',
  'userId',
] as const

export function groupedConnectionProfiles(
  profiles: ConnectionProfile[],
  activeProfileId: string,
): ConnectionProfile[][] {
  const groups = new Map<string, ConnectionProfile[]>()
  const sorted = [...profiles].sort((a, b) =>
    a.id === activeProfileId ? -1 : b.id === activeProfileId ? 1 : 0,
  )
  for (const profile of sorted) {
    // Account and user IDs may still be unverified defaults. A different key
    // must remain a separate connection, even when those labels are identical.
    const identity = connectionProfileFingerprint(profile.connection)
    const group = groups.get(identity)
    if (group) group.push(profile)
    else groups.set(identity, [profile])
  }
  return [...groups.values()]
}

export function ConnectionProfilesList() {
  const { t } = useTranslation(['studio', 'settings'])
  const {
    profiles = [],
    activeProfileId,
    saveProfile,
    activateProfile,
    deleteProfile,
  } = useAppConnection()
  const [draft, setDraft] = React.useState<ConnectionProfile | null>(null)
  const [error, setError] = React.useState('')
  const [deleting, setDeleting] = React.useState<ConnectionProfile | null>(null)
  const openEditor = (profile: ConnectionProfile) => {
    setError('')
    setDraft(profile)
  }
  function save(event: React.FormEvent) {
    event.preventDefault()
    if (!draft) return
    if (!isValidConnectionUrl(draft.connection.baseUrl)) {
      setError(t('profiles.invalidUrl'))
      return
    }
    const connection = normalizeProfileConnection(draft.connection)
    saveProfile({
      ...draft,
      connection: {
        ...connection,
        adminApiKey:
          connection.serviceSelection === 'volcengine'
            ? ''
            : connection.adminApiKey,
      },
    })
    setDraft(null)
  }
  const identityLabel = (profile: ConnectionProfile) =>
    profile.connection.serviceSelection === 'volcengine'
      ? formatHostedIdentity(
          profile.connection.accountId,
          profile.connection.userId,
        )
      : `${profile.connection.accountId}/${profile.connection.userId}`
  const renderProfile = (profile: ConnectionProfile) => (
    <div
      key={profile.id}
      className="flex min-w-0 flex-col items-start justify-between gap-3 rounded-md border p-3 sm:flex-row sm:items-center"
    >
      <div className="w-full min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{profile.name}</span>
          {identityLabel(profile) && (
            <span
              className="font-mono text-xs text-muted-foreground"
              title={identityLabel(profile) ?? undefined}
            >
              [{identityLabel(profile)}]
            </span>
          )}
          <Badge variant="outline">
            {t(`providers.${profile.connection.serviceSelection}`)}
          </Badge>
          {profile.id === activeProfileId && (
            <Badge
              variant="outline"
              className="border-emerald-600/20 bg-emerald-600/10 text-emerald-700 dark:border-emerald-400/25 dark:bg-emerald-400/10 dark:text-emerald-400"
            >
              {t('profiles.active')}
            </Badge>
          )}
        </div>
        <p className="mt-1 break-all text-sm text-muted-foreground">
          {profile.connection.baseUrl}
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
          disabled={profile.id === activeProfileId}
          onClick={() => activateProfile(profile.id)}
        >
          {t('profiles.activate')}
        </Button>
        <Button variant="outline" onClick={() => openEditor(profile)}>
          {t('profiles.edit')}
        </Button>
        <Button
          variant="ghost"
          disabled={profile.id === activeProfileId}
          onClick={() => setDeleting(profile)}
        >
          {t('profiles.delete')}
        </Button>
      </div>
    </div>
  )
  return (
    <>
      <Card>
        <CardHeader className="flex flex-wrap items-center justify-between gap-3">
          <CardTitle>{t('profiles.title')}</CardTitle>
          <Button
            onClick={() =>
              openEditor({
                id: createRandomUuid(),
                name: '',
                connection: {
                  serviceSelection: 'opensource',
                  baseUrl: '',
                  apiKey: '',
                  adminApiKey: '',
                  accountId: 'default',
                  userId: 'default',
                },
              })
            }
          >
            {t('profiles.add')}
          </Button>
        </CardHeader>
        <CardContent className="grid gap-3">
          <p className="text-sm text-muted-foreground">{t('profiles.hint')}</p>
          {groupedConnectionProfiles(profiles, activeProfileId).map((group) => {
            if (group.length === 1) return renderProfile(group[0])
            const primary = group[0]
            return (
              <details key={primary.id} className="group rounded-md border">
                <summary className="flex cursor-pointer list-none items-center justify-between gap-3 p-3 [&::-webkit-details-marker]:hidden">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium">{primary.name}</span>
                      {identityLabel(primary) && (
                        <span className="font-mono text-xs text-muted-foreground">
                          [{identityLabel(primary)}]
                        </span>
                      )}
                      <Badge variant="outline">
                        {t(`providers.${primary.connection.serviceSelection}`)}
                      </Badge>
                      {group.some(
                        (profile) => profile.id === activeProfileId,
                      ) && (
                        <Badge
                          variant="outline"
                          className="border-emerald-600/20 bg-emerald-600/10 text-emerald-700 dark:border-emerald-400/25 dark:bg-emerald-400/10 dark:text-emerald-400"
                        >
                          {t('profiles.active')}
                        </Badge>
                      )}
                      <span className="text-xs text-muted-foreground">
                        {t('profiles.title')} · {group.length}
                      </span>
                    </div>
                    <p className="mt-1 break-all text-sm text-muted-foreground">
                      {primary.connection.baseUrl}
                    </p>
                  </div>
                  <ChevronDownIcon className="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-180" />
                </summary>
                <div className="grid gap-2 border-t p-2">
                  {group.map(renderProfile)}
                </div>
              </details>
            )
          })}
        </CardContent>
      </Card>
      <Dialog
        open={Boolean(draft)}
        onOpenChange={(open) => {
          if (!open) setDraft(null)
        }}
      >
        <DialogContent className="sm:max-w-lg">
          <DialogHeader className="pr-8">
            <DialogTitle>
              {t(
                profiles.some((p) => p.id === draft?.id)
                  ? 'profiles.edit'
                  : 'profiles.add',
              )}
            </DialogTitle>
            <DialogDescription className="text-xs leading-relaxed">
              {t('profiles.saveHint')}
            </DialogDescription>
          </DialogHeader>
          {draft && (
            <form onSubmit={save} className="grid gap-5">
              <Field className="gap-2">
                <FieldLabel htmlFor="profile-name">
                  {t('profiles.name')}
                </FieldLabel>
                <Input
                  id="profile-name"
                  className="h-10"
                  required
                  value={draft.name}
                  onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                  {...PLAIN_INPUT_PROPS}
                />
              </Field>
              <Field className="gap-2">
                <FieldLabel htmlFor="profile-provider">
                  {t('serviceType')}
                </FieldLabel>
                <Select
                  value={draft.connection.serviceSelection}
                  items={[
                    { value: 'opensource', label: t('providers.opensource') },
                    { value: 'volcengine', label: t('providers.volcengine') },
                  ]}
                  onValueChange={(value) => {
                    if (value !== 'opensource' && value !== 'volcengine') return
                    setDraft({
                      ...draft,
                      connection: {
                        ...draft.connection,
                        serviceSelection: value,
                      },
                    })
                  }}
                >
                  <SelectTrigger
                    id="profile-provider"
                    className="w-full data-[size=default]:h-10"
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent align="start">
                    <SelectItem value="opensource">
                      {t('providers.opensource')}
                    </SelectItem>
                    <SelectItem value="volcengine">
                      {t('providers.volcengine')}
                    </SelectItem>
                  </SelectContent>
                </Select>
              </Field>
              {PROFILE_FIELDS.filter(
                (field) =>
                  draft.connection.serviceSelection === 'opensource' ||
                  field === 'baseUrl' ||
                  field === 'apiKey',
              ).map((key) => {
                return (
                  <Field key={key} className="gap-2">
                    <FieldLabel htmlFor={`profile-${key}`}>
                      {t(`profiles.fields.${key}`)}
                    </FieldLabel>
                    <Input
                      id={`profile-${key}`}
                      className="h-10"
                      required={key === 'baseUrl'}
                      type={
                        key === 'apiKey' || key === 'adminApiKey'
                          ? 'password'
                          : 'text'
                      }
                      value={draft.connection[key]}
                      onChange={(e) =>
                        setDraft({
                          ...draft,
                          connection: {
                            ...draft.connection,
                            [key]: e.target.value,
                          },
                        })
                      }
                      {...PLAIN_INPUT_PROPS}
                    />
                  </Field>
                )
              })}
              {error && (
                <p role="alert" className="text-sm text-destructive">
                  {error}
                </p>
              )}
              <div className="flex justify-end gap-2 border-t pt-4">
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setDraft(null)}
                >
                  {t('profiles.cancel')}
                </Button>
                <Button type="submit" disabled={!draft.name.trim()}>
                  {t('profiles.save')}
                </Button>
              </div>
            </form>
          )}
        </DialogContent>
      </Dialog>
      <Dialog
        open={Boolean(deleting)}
        onOpenChange={(open) => {
          if (!open) setDeleting(null)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('profiles.delete')}</DialogTitle>
            <DialogDescription>
              {t('profiles.deleteConfirm', { name: deleting?.name })}
            </DialogDescription>
          </DialogHeader>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setDeleting(null)}>
              {t('profiles.cancel')}
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                if (deleting) deleteProfile(deleting.id)
                setDeleting(null)
              }}
            >
              {t('profiles.delete')}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}
