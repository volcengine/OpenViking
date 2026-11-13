import { ErrorMessage } from '#/shared/components/error-message'
import { SetupIdentityDirectory } from '#/features/connection/components/setup-identity-directory'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { flushCredentialStorage } from '#/features/connection/lib/credential-storage'
import { formatHostedIdentity } from '#/features/connection/lib/identity-label'
import {
  CLOUD_SETUP_URL,
  StudioSetupError,
  StudioSetupRequestError,
  verifyStudioSetup,
} from '#/features/connection/lib/onboarding'
import type { ConnectionProfile } from '#/features/connection/lib/profiles'
import {
  connectionProfileFingerprint,
  isValidConnectionUrl,
  normalizeProfileConnection,
} from '#/features/connection/lib/profiles'
import { newConnectionPath } from '#/features/connection/lib/studio-mode'
import { createRandomUuid } from '#/shared/lib/browser-crypto'
import { PLAIN_INPUT_PROPS } from '#/shared/lib/form-input'
import { Badge } from '#/shared/ui/badge'
import { Button, buttonVariants } from '#/shared/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '#/shared/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '#/shared/ui/dialog'
import { Field, FieldLabel } from '#/shared/ui/field'
import { Input } from '#/shared/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '#/shared/ui/select'
import { Link, useNavigate } from '@tanstack/react-router'
import { ChevronDownIcon } from 'lucide-react'
import * as React from 'react'
import { useTranslation } from 'react-i18next'

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

export function ConnectionProfilesList({ editorId }: { editorId?: string }) {
  const { t } = useTranslation(['studio', 'settings'])
  const navigate = useNavigate()
  const {
    profiles = [],
    connection: currentConnection,
    studioMode,
    activeProfileId,
    saveProfile,
    activateProfile,
    deleteProfile,
  } = useAppConnection()
  const settingsPath =
    studioMode === 'management' ? '/admin/settings' : '/settings'
  const isNew = editorId === 'new'
  const original = profiles.find((profile) => profile.id === editorId)
  const [draft, setDraft] = React.useState<ConnectionProfile | null>(() =>
    isNew
      ? {
          id: createRandomUuid(),
          name: '',
          connection: {
            serviceSelection:
              currentConnection.serviceSelection === 'volcengine'
                ? 'volcengine'
                : 'opensource',
            baseUrl: currentConnection.baseUrl,
            adminApiKey: '',
            apiKey: '',
            accountId: 'default',
            userId: 'default',
          },
        }
      : original
        ? { ...original, connection: { ...original.connection } }
        : null,
  )
  const [error, setError] = React.useState<string | Error>('')
  const [directoryIdentitySelected, setDirectoryIdentitySelected] =
    React.useState(false)
  const [saving, setSaving] = React.useState(false)
  const saveController = React.useRef<AbortController | null>(null)
  const currentDraft = React.useRef(draft)
  currentDraft.current = draft
  React.useEffect(() => {
    saveController.current?.abort()
    setSaving(false)
    return () => saveController.current?.abort()
  }, [draft])
  const [directoryScope, setDirectoryScope] = React.useState<string | null>(
    null,
  )
  const currentDirectoryScope = draft
    ? JSON.stringify([
        draft.id,
        draft.connection.serviceSelection,
        draft.connection.baseUrl,
        draft.connection.adminApiKey,
      ])
    : null
  const hasControlKey =
    draft?.connection.serviceSelection === 'opensource' &&
    Boolean(draft.connection.adminApiKey.trim())
  const [deleting, setDeleting] = React.useState<ConnectionProfile | null>(null)
  const openSourceUrlRef = React.useRef(
    draft?.connection.serviceSelection === 'volcengine'
      ? ''
      : draft?.connection.baseUrl || '',
  )
  function closeEditor() {
    setDraft(null)
    void navigate({ to: settingsPath })
  }
  async function save(event: React.FormEvent) {
    event.preventDefault()
    if (!draft || saving) return
    if (!isValidConnectionUrl(draft.connection.baseUrl)) {
      setError(t('profiles.invalidUrl'))
      return
    }
    let connection = normalizeProfileConnection(draft.connection)
    const changed =
      !original ||
      connectionProfileFingerprint(original.connection) !==
        connectionProfileFingerprint(connection)
    if (
      isNew ||
      (changed && !(directoryIdentitySelected && !connection.apiKey.trim())) ||
      (directoryIdentitySelected && connection.apiKey.trim())
    ) {
      const controller = new AbortController()
      saveController.current = controller
      setSaving(true)
      setError('')
      try {
        const verified = await verifyStudioSetup(connection, controller.signal)
        if (controller.signal.aborted || currentDraft.current !== draft) return
        if (
          directoryIdentitySelected &&
          (verified.accountId !== connection.accountId ||
            verified.userId !== connection.userId)
        ) {
          setError(t('onboarding:directoryIdentityMismatch'))
          return
        }
        connection = verified
      } catch (cause) {
        if (!controller.signal.aborted && currentDraft.current === draft) {
          setError(
            cause instanceof StudioSetupError
              ? t(`onboarding:${cause.code}`)
              : cause instanceof StudioSetupRequestError
                ? cause
                : new StudioSetupRequestError('data', cause, connection),
          )
        }
        return
      } finally {
        if (saveController.current === controller) {
          saveController.current = null
          setSaving(false)
        }
      }
    }
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
    if (isNew) activateProfile(draft.id)
    try {
      await flushCredentialStorage()
      closeEditor()
    } catch {
      setError(t('credentialStorage.saveError'))
    }
  }
  const identityLabel = (profile: ConnectionProfile) =>
    studioMode === 'management'
      ? profile.connection.accountId
      : profile.connection.serviceSelection === 'volcengine'
        ? formatHostedIdentity(
            profile.connection.accountId,
            profile.connection.userId,
          )
        : `${profile.connection.accountId}/${profile.connection.userId}`
  const profileName = (profile: ConnectionProfile) =>
    studioMode === 'management' &&
    profile.name ===
      `${profile.connection.accountId}/${profile.connection.userId}`
      ? t(`providers.${profile.connection.serviceSelection}`)
      : profile.name
  const renderProfile = (profile: ConnectionProfile) => (
    <div
      key={profile.id}
      className="flex min-w-0 flex-col items-start justify-between gap-3 rounded-md border p-3 sm:flex-row sm:items-center"
    >
      <div className="w-full min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{profileName(profile)}</span>
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
        <Button
          variant="outline"
          onClick={() => {
            void navigate({
              to:
                studioMode === 'management'
                  ? '/admin/connections/$profileId'
                  : '/connections/$profileId',
              params: { profileId: profile.id },
            })
          }}
        >
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
      {!editorId ? (
        <Card>
          <CardHeader className="flex flex-wrap items-center justify-between gap-3">
            <CardTitle>{t('profiles.title')}</CardTitle>
            <Link
              to={newConnectionPath(settingsPath)}
              className={buttonVariants()}
            >
              {t('profiles.add')}
            </Link>
          </CardHeader>
          <CardContent className="grid gap-3">
            <p className="text-sm text-muted-foreground">
              {t('profiles.hint')}
            </p>
            {groupedConnectionProfiles(profiles, activeProfileId).map(
              (group) => {
                if (group.length === 1) return renderProfile(group[0])
                const primary = group[0]
                return (
                  <details key={primary.id} className="group rounded-md border">
                    <summary className="flex cursor-pointer list-none items-center justify-between gap-3 p-3 [&::-webkit-details-marker]:hidden">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-medium">
                            {profileName(primary)}
                          </span>
                          {identityLabel(primary) && (
                            <span className="font-mono text-xs text-muted-foreground">
                              [{identityLabel(primary)}]
                            </span>
                          )}
                          <Badge variant="outline">
                            {t(
                              `providers.${primary.connection.serviceSelection}`,
                            )}
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
              },
            )}
          </CardContent>
        </Card>
      ) : null}
      {editorId ? (
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-5">
          <Link
            to={settingsPath}
            className="w-fit text-sm text-muted-foreground hover:text-foreground"
          >
            {t('profiles.back')}
          </Link>
          <h1 className="text-2xl font-semibold">
            {t(isNew ? 'profiles.add' : 'profiles.edit')}
          </h1>
          {draft ? (
            <Card>
              <CardHeader>
                <p className="text-sm text-muted-foreground">
                  {t('profiles.editorHint')}
                </p>
              </CardHeader>
              <CardContent>
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
                      onChange={(e) =>
                        setDraft({ ...draft, name: e.target.value })
                      }
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
                        {
                          value: 'opensource',
                          label: t('providers.opensource'),
                        },
                        {
                          value: 'volcengine',
                          label: t('providers.volcengine'),
                        },
                      ]}
                      onValueChange={(value) => {
                        if (value !== 'opensource' && value !== 'volcengine')
                          return
                        setDirectoryIdentitySelected(false)
                        if (
                          draft.connection.serviceSelection !== 'volcengine'
                        ) {
                          openSourceUrlRef.current = draft.connection.baseUrl
                        }
                        setDraft({
                          ...draft,
                          connection: {
                            ...draft.connection,
                            baseUrl:
                              value === 'volcengine'
                                ? CLOUD_SETUP_URL
                                : openSourceUrlRef.current,
                            serviceSelection: value,
                            apiKey: '',
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
                      (draft.connection.serviceSelection === 'opensource' ||
                        field === 'baseUrl' ||
                        field === 'apiKey') &&
                      !(
                        hasControlKey &&
                        (field === 'accountId' || field === 'userId')
                      ),
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
                          readOnly={
                            key === 'baseUrl' &&
                            draft.connection.serviceSelection === 'volcengine'
                          }
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
                        {key === 'adminApiKey' ? (
                          <p className="text-xs text-muted-foreground">
                            {t('profiles.managementHint')}
                          </p>
                        ) : null}
                      </Field>
                    )
                  })}
                  {hasControlKey && (
                    <>
                      <Button
                        type="button"
                        variant="outline"
                        onClick={() => setDirectoryScope(currentDirectoryScope)}
                      >
                        {t('onboarding:verify')}
                      </Button>
                      {directoryScope === currentDirectoryScope && (
                        <SetupIdentityDirectory
                          key={currentDirectoryScope}
                          connection={draft.connection}
                          disabled={false}
                          onSelect={({ accountId, userId }) => {
                            setDirectoryIdentitySelected(true)
                            setDraft((current) =>
                              current
                                ? {
                                    ...current,
                                    connection: {
                                      ...current.connection,
                                      accountId,
                                      userId,
                                      apiKey:
                                        current.connection.accountId ===
                                          accountId &&
                                        current.connection.userId === userId
                                          ? current.connection.apiKey
                                          : '',
                                    },
                                  }
                                : current,
                            )
                          }}
                        />
                      )}
                    </>
                  )}
                  {error && <ErrorMessage error={error} />}
                  <div className="flex justify-end gap-2 border-t pt-4">
                    <Button
                      type="button"
                      variant="outline"
                      onClick={closeEditor}
                    >
                      {t('profiles.cancel')}
                    </Button>
                    <Button
                      type="submit"
                      disabled={saving || !draft.name.trim()}
                    >
                      {t(
                        saving
                          ? 'onboarding:verifying'
                          : isNew
                            ? 'profiles.saveActivate'
                            : 'profiles.save',
                      )}
                    </Button>
                  </div>
                </form>
              </CardContent>
            </Card>
          ) : (
            <p role="alert">{t('profiles.notFound')}</p>
          )}
        </div>
      ) : null}
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
