import brandLockup from '#/assets/onboarding/brand.svg'
import serverIcon from '#/assets/onboarding/server.svg'
import eyeIcon from '#/assets/onboarding/eye.svg'
import arrowRightIcon from '#/assets/onboarding/arrow-right.svg'
import teamIllustration from '#/assets/onboarding-team.png'
import volcengineMark from '#/assets/volcengine-mark.png'
import './studio-setup.css'
import { ErrorMessage } from '#/shared/components/error-message'
import { SetupIdentityDirectory } from '#/features/connection/components/setup-identity-directory'
import type { ConnectionDraft } from '#/features/connection/lib/connection-runtime'
import { readCredentialStorage } from '#/features/connection/lib/credential-storage'
import {
  CLOUD_SETUP_URL,
  StudioSetupError,
  StudioSetupRequestError,
  completeStudioSetup,
  savedSetupConnections,
  studioSetupDestination,
  verifyStudioSetup,
} from '#/features/connection/lib/onboarding'
import {
  connectionProfileFingerprint,
  isValidConnectionUrl,
} from '#/features/connection/lib/profiles'
import { PLAIN_INPUT_PROPS } from '#/shared/lib/form-input'
import { Button } from '#/shared/ui/button'
import { Input } from '#/shared/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '#/shared/ui/select'
import {
  ChevronDownIcon,
  EyeOffIcon,
  Loader2Icon,
  ServerIcon,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

const LANGUAGES = [
  ['zh-CN', '简体中文'],
  ['en', 'English'],
] as const
const PROVIDERS = [
  ['opensource', ServerIcon],
  ['volcengine', ServerIcon],
] as const
const ADVANCED_FIELDS = ['accountId', 'userId'] as const
const NEW_CONNECTION = 'new'
type SavedConnection = ReturnType<typeof savedSetupConnections>[number]

function withoutCredentials(connection: ConnectionDraft): ConnectionDraft {
  return { ...connection, apiKey: '', adminApiKey: '' }
}

function savedConnectionLabel(
  profile: SavedConnection,
  profiles: SavedConnection[],
  providerLabel: string,
): string {
  const domain = new URL(profile.connection.baseUrl).host
  const title = `${providerLabel} · ${domain}`
  const sameLabel = profiles.filter(
    (candidate) => new URL(candidate.connection.baseUrl).host === domain,
  )
  if (sameLabel.length < 2) return title
  return `${title} (${sameLabel.findIndex((candidate) => candidate.id === profile.id) + 1})`
}

function newConnectionDraft(
  provider: (typeof PROVIDERS)[number][0],
): ConnectionDraft {
  return {
    serviceSelection: provider,
    baseUrl:
      provider === 'volcengine'
        ? CLOUD_SETUP_URL
        : window.location.pathname.startsWith('/studio')
          ? window.location.origin
          : '',
    apiKey: '',
    adminApiKey: '',
    accountId: 'default',
    userId: 'default',
  }
}

export function StudioSetup({
  onComplete,
  onCancel,
  canReturnToWorkspace = false,
}: {
  onComplete: (connection: ConnectionDraft) => void
  onCancel?: () => void
  canReturnToWorkspace?: boolean
}) {
  const { t, i18n } = useTranslation(['onboarding', 'studio', 'settings'])
  const [showKey, setShowKey] = useState(false)
  const [showAddress, setShowAddress] = useState(true)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [controlVerified, setControlVerified] = useState(false)
  const [directoryOpen, setDirectoryOpen] = useState(false)
  const [selectedDirectoryIdentity, setSelectedDirectoryIdentity] =
    useState(false)
  const [activeProfileId] = useState(() => {
    try {
      return JSON.parse(
        readCredentialStorage('ov_console_connection_profiles') || '{}',
      ).activeId as string | undefined
    } catch {
      return undefined
    }
  })
  const [savedConnections] = useState(() => {
    const profiles = savedSetupConnections()
    if (
      import.meta.env.DEV &&
      profiles.length > 0 &&
      !profiles.some(
        (profile) => profile.connection.serviceSelection === 'opensource',
      ) &&
      ['localhost', '127.0.0.1'].includes(window.location.hostname)
    ) {
      profiles.push({
        id: 'local-openviking',
        name: 'local-openviking',
        connection: {
          ...newConnectionDraft('opensource'),
          baseUrl: 'http://127.0.0.1:1933',
        },
      })
    }
    const seen = new Set<string>()
    return profiles
      .sort((a, b) =>
        a.id === activeProfileId ? -1 : b.id === activeProfileId ? 1 : 0,
      )
      .filter((profile) => {
        const connection = profile.connection
        const address = new URL(connection.baseUrl).href.replace(/\/+$/, '')
        const identity = JSON.stringify([connection.serviceSelection, address])
        if (seen.has(identity)) return false
        seen.add(identity)
        return true
      })
  })
  const initialProfile = savedConnections.at(0)
  const [selectedConnectionId, setSelectedConnectionId] = useState(() =>
    initialProfile?.connection.serviceSelection === 'opensource'
      ? initialProfile.id
      : NEW_CONNECTION,
  )
  const [draft, setDraft] = useState<ConnectionDraft>(() => {
    if (initialProfile) {
      return withoutCredentials(initialProfile.connection)
    }
    return newConnectionDraft('opensource')
  })
  const newDraftsRef = useRef<
    Partial<Record<(typeof PROVIDERS)[number][0], ConnectionDraft>>
  >({})
  const providerSelectionsRef = useRef<
    Partial<Record<(typeof PROVIDERS)[number][0], string>>
  >({})
  const [verified, setVerified] = useState<ConnectionDraft | null>(null)
  const providerConnections = savedConnections
    .map((profile) =>
      profile.id === 'local-openviking'
        ? { ...profile, name: t('studio:localConnection') }
        : profile,
    )
    .filter(
      (profile) =>
        profile.connection.serviceSelection === draft.serviceSelection,
    )
  const selectedProfile = providerConnections.find(
    (profile) => profile.id === selectedConnectionId,
  )
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | Error>('')
  const connectionRevisionRef = useRef(0)
  const verifiedSelectionRef = useRef<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const keyInputRef = useRef<HTMLInputElement>(null)
  const urlInputRef = useRef<HTMLInputElement>(null)
  const focusKeyOnProviderSwitchRef = useRef(false)
  const headingRef = useRef<HTMLHeadingElement>(null)
  useEffect(() => {
    if (urlInputRef.current) urlInputRef.current.focus()
    else headingRef.current?.focus()
    return () => abortRef.current?.abort()
  }, [])
  useEffect(() => {
    if (
      draft.serviceSelection === 'volcengine' &&
      focusKeyOnProviderSwitchRef.current
    ) {
      keyInputRef.current?.focus()
    }
    focusKeyOnProviderSwitchRef.current = false
  }, [draft.serviceSelection])
  const language = (i18n.resolvedLanguage ?? i18n.language).startsWith('zh')
    ? 'zh-CN'
    : 'en'
  function update(next: Partial<ConnectionDraft>) {
    if ('baseUrl' in next || 'adminApiKey' in next) setControlVerified(false)
    if (
      'baseUrl' in next ||
      'adminApiKey' in next ||
      'accountId' in next ||
      'userId' in next
    )
      setSelectedDirectoryIdentity(false)
    connectionRevisionRef.current += 1
    verifiedSelectionRef.current = null
    setDraft((value) => ({ ...value, ...next }))
    setVerified(null)
    setError('')
  }
  function selectConnection(id: string) {
    if (busy || id === selectedConnectionId) return
    connectionRevisionRef.current += 1
    verifiedSelectionRef.current = null
    const provider =
      draft.serviceSelection === 'volcengine' ? 'volcengine' : 'opensource'
    if (selectedConnectionId === NEW_CONNECTION)
      newDraftsRef.current[provider] = draft
    setControlVerified(false)
    setSelectedDirectoryIdentity(false)
    setSelectedConnectionId(id)
    providerSelectionsRef.current[provider] = id
    setDraft(
      id === NEW_CONNECTION
        ? (newDraftsRef.current[provider] ?? newConnectionDraft(provider))
        : withoutCredentials(
            providerConnections.find((profile) => profile.id === id)!
              .connection,
          ),
    )
    setVerified(null)
    setError('')
  }
  function switchProvider(provider: (typeof PROVIDERS)[number][0]) {
    if (busy) return
    const current =
      draft.serviceSelection === 'volcengine' ? 'volcengine' : 'opensource'
    if (provider === current) return
    focusKeyOnProviderSwitchRef.current = provider === 'volcengine'
    connectionRevisionRef.current += 1
    verifiedSelectionRef.current = null
    providerSelectionsRef.current[current] = selectedConnectionId
    if (selectedConnectionId === NEW_CONNECTION)
      newDraftsRef.current[current] = draft
    const nextId =
      provider === 'volcengine'
        ? NEW_CONNECTION
        : (providerSelectionsRef.current[provider] ??
          savedConnections.find(
            (profile) => profile.connection.serviceSelection === provider,
          )?.id ??
          NEW_CONNECTION)
    setControlVerified(false)
    setSelectedDirectoryIdentity(false)
    setSelectedConnectionId(nextId)
    const savedHostedConnection = savedConnections.find(
      (profile) => profile.connection.serviceSelection === 'volcengine',
    )?.connection
    setDraft(
      nextId === NEW_CONNECTION
        ? (newDraftsRef.current[provider] ??
            (provider === 'volcengine' && savedHostedConnection
              ? withoutCredentials(savedHostedConnection)
              : newConnectionDraft(provider)))
        : withoutCredentials(
            savedConnections.find((profile) => profile.id === nextId)!
              .connection,
          ),
    )
    setVerified(null)
    setError('')
  }
  async function enterWorkspace(connection: ConnectionDraft) {
    const matchingProfile = savedSetupConnections().find(
      (profile) =>
        connectionProfileFingerprint(profile.connection) ===
        connectionProfileFingerprint(connection),
    )
    await completeStudioSetup(
      connection,
      t(`studio:providers.${connection.serviceSelection}`),
      matchingProfile?.id,
    )
    onComplete(connection)
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (busy) return
    setError('')
    if (verified) {
      if (verifiedSelectionRef.current !== selectedConnectionId) {
        setVerified(null)
        setError(t('savedConnectionUnavailable'))
        return
      }
      setBusy(true)
      try {
        await enterWorkspace(verified)
      } catch {
        setError(t('storageError'))
      } finally {
        setBusy(false)
      }
      return
    }
    if (!isValidConnectionUrl(draft.baseUrl)) {
      setError(t('studio:profiles.invalidUrl'))
      return
    }
    if (draft.serviceSelection === 'volcengine' && !draft.apiKey.trim()) {
      setError(t('keyRequired'))
      keyInputRef.current?.focus()
      return
    }
    const controller = new AbortController()
    const connectionRevision = connectionRevisionRef.current
    abortRef.current = controller
    setBusy(true)
    try {
      const result = await verifyStudioSetup(draft, controller.signal)
      if (
        !controller.signal.aborted &&
        connectionRevision === connectionRevisionRef.current
      ) {
        if (
          selectedDirectoryIdentity &&
          (result.accountId !== draft.accountId ||
            result.userId !== draft.userId)
        ) {
          setError(t('directoryIdentityMismatch'))
          return
        }
        setControlVerified(
          result.serviceSelection === 'opensource' &&
            Boolean(result.adminApiKey),
        )
        setDraft(result)
        verifiedSelectionRef.current = selectedConnectionId
        setVerified(result)
        // Explicit management credentials keep the identity picker available.
        if (!result.adminApiKey || !draft.adminApiKey) {
          try {
            await enterWorkspace(result)
          } catch {
            setError(t('storageError'))
          }
        }
      }
    } catch (err) {
      if (!controller.signal.aborted)
        setError(
          err instanceof StudioSetupError
            ? t(err.code)
            : err instanceof StudioSetupRequestError
              ? err
              : new StudioSetupRequestError('data', err, draft),
        )
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null
        setBusy(false)
      }
    }
  }
  return (
    <main className="studio-setup" data-provider={draft.serviceSelection}>
      <section className="setup-connection">
        <header className="setup-header">
          <img
            src={brandLockup}
            alt={t('productName')}
            className="setup-brand"
          />
          <div
            className="setup-languages"
            role="group"
            aria-label={t('language')}
          >
            {LANGUAGES.map(([value, label]) => (
              <button
                type="button"
                key={value}
                aria-pressed={language === value}
                onClick={() => {
                  setError('')
                  void i18n.changeLanguage(value)
                  document.documentElement.lang = value
                }}
              >
                {label}
              </button>
            ))}
          </div>
          {onCancel && canReturnToWorkspace ? (
            <Button
              type="button"
              variant="ghost"
              className="setup-cancel"
              onClick={() => {
                connectionRevisionRef.current += 1
                abortRef.current?.abort()
                onCancel()
              }}
            >
              {t('cancel')}
            </Button>
          ) : null}
        </header>
        <div className="setup-content">
          <div className="setup-heading">
            <h1 ref={headingRef} tabIndex={-1}>
              {t('connectTitle')}
            </h1>
            <p>{t('connectDescription')}</p>
          </div>
          <form
            onSubmit={(event) => void submit(event)}
            className="setup-form"
            noValidate
          >
            <fieldset disabled={busy} className="setup-fields">
              <fieldset className="setup-provider">
                <legend>{t('studio:serviceType')}</legend>
                <div className="setup-provider-options">
                  {PROVIDERS.map(([provider]) => (
                    <label key={provider}>
                      <input
                        className="sr-only"
                        type="radio"
                        name="provider"
                        value={provider}
                        checked={draft.serviceSelection === provider}
                        onChange={() => switchProvider(provider)}
                      />
                      {provider === 'volcengine' ? (
                        <img src={volcengineMark} alt="" />
                      ) : (
                        <img
                          src={serverIcon}
                          alt=""
                          className="setup-server-icon"
                        />
                      )}
                      {t(
                        provider === 'opensource'
                          ? 'privateDeployment'
                          : 'hostedService',
                      )}
                    </label>
                  ))}
                </div>
                {draft.serviceSelection === 'volcengine' ? (
                  <p
                    className="setup-hosted-endpoint"
                    aria-label={t('hostedEndpoint')}
                  >
                    {t('hostedAddress', { address: draft.baseUrl })}
                  </p>
                ) : null}
              </fieldset>
              {draft.serviceSelection === 'opensource' &&
              providerConnections.length > 0 ? (
                <div className="setup-field">
                  <label>{t('connectionChoice')}</label>
                  <Select
                    value={selectedConnectionId}
                    onValueChange={(value) => {
                      if (value) selectConnection(value)
                    }}
                  >
                    <SelectTrigger
                      aria-label={t('connectionChoice')}
                      className="h-12 w-full rounded-lg"
                    >
                      <SelectValue>
                        {selectedProfile
                          ? savedConnectionLabel(
                              selectedProfile,
                              providerConnections,
                              t('openSourceConnection'),
                            )
                          : t('newConnection')}
                      </SelectValue>
                    </SelectTrigger>
                    <SelectContent
                      align="start"
                      className="w-[var(--anchor-width)]"
                    >
                      {providerConnections.map((profile) => (
                        <SelectItem
                          key={profile.id}
                          value={profile.id}
                          title={profile.connection.baseUrl}
                        >
                          {savedConnectionLabel(
                            profile,
                            providerConnections,
                            t('openSourceConnection'),
                          )}
                        </SelectItem>
                      ))}
                      <SelectItem value={NEW_CONNECTION}>
                        {t('newConnection')}
                      </SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              ) : null}
              {draft.serviceSelection === 'opensource' ? (
                <div className="setup-field">
                  <label htmlFor="setup-new-url" id="setup-url-label">
                    {t('hostedEndpoint')}
                  </label>
                  {selectedProfile ? (
                    <p className="setup-hosted-address" title={draft.baseUrl}>
                      {draft.baseUrl}
                    </p>
                  ) : (
                    <div className="setup-key-input">
                      <Input
                        ref={urlInputRef}
                        id="setup-new-url"
                        type={showAddress ? 'url' : 'password'}
                        value={draft.baseUrl}
                        aria-label={t('settings:fields.baseUrl')}
                        placeholder={t('addressPlaceholder')}
                        aria-invalid={
                          Boolean(draft.baseUrl) &&
                          !isValidConnectionUrl(draft.baseUrl)
                        }
                        onChange={(event) =>
                          update({ baseUrl: event.target.value })
                        }
                        {...PLAIN_INPUT_PROPS}
                      />
                      <button
                        type="button"
                        aria-label={t(
                          showAddress ? 'hideAddress' : 'showAddress',
                        )}
                        aria-pressed={!showAddress}
                        onClick={() => setShowAddress((show) => !show)}
                      >
                        {showAddress ? (
                          <img src={eyeIcon} alt="" />
                        ) : (
                          <EyeOffIcon aria-hidden="true" />
                        )}
                      </button>
                    </div>
                  )}
                </div>
              ) : null}
              <div className="setup-field">
                <label htmlFor="setup-key">{t('keyLabel')}</label>
                <div className="setup-key-input">
                  <Input
                    ref={keyInputRef}
                    id="setup-key"
                    name="apiKey"
                    type={showKey ? 'text' : 'password'}
                    placeholder={t('keyPlaceholder')}
                    value={draft.apiKey}
                    aria-invalid={error === t('keyRequired')}
                    aria-describedby={
                      error === t('keyRequired') ? 'setup-key-error' : undefined
                    }
                    onChange={(event) => update({ apiKey: event.target.value })}
                    {...PLAIN_INPUT_PROPS}
                    autoComplete="off"
                  />
                  <button
                    type="button"
                    aria-label={t(showKey ? 'hideKey' : 'showKey')}
                    aria-pressed={showKey}
                    onClick={() => setShowKey((show) => !show)}
                  >
                    {showKey ? (
                      <EyeOffIcon aria-hidden="true" />
                    ) : (
                      <img src={eyeIcon} alt="" />
                    )}
                  </button>
                </div>
                {error === t('keyRequired') ? (
                  <p
                    id="setup-key-error"
                    role="alert"
                    className="text-sm text-destructive"
                  >
                    {error}
                  </p>
                ) : null}
              </div>
              {draft.serviceSelection === 'opensource' &&
              !controlVerified &&
              canReturnToWorkspace ? (
                <div>
                  <button
                    type="button"
                    aria-expanded={advancedOpen}
                    aria-controls="setup-advanced-fields"
                    onClick={() => setAdvancedOpen((open) => !open)}
                    className="flex items-center gap-1 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:rounded-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                  >
                    <ChevronDownIcon
                      aria-hidden="true"
                      className={`size-3 transition-transform duration-300 motion-reduce:transition-none ${advancedOpen ? 'rotate-180' : ''}`}
                    />
                    {t('advanced')}
                  </button>
                  <div
                    id="setup-advanced-fields"
                    aria-hidden={!advancedOpen}
                    inert={!advancedOpen}
                    className={`grid transition-[grid-template-rows,opacity] duration-300 ease-out motion-reduce:transition-none ${advancedOpen ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0'}`}
                  >
                    <div className="min-h-0 overflow-hidden">
                      <div className="grid gap-3 pt-3">
                        <div className="grid gap-2 text-sm font-medium">
                          <label htmlFor="setup-adminApiKey">
                            {t('settings:fields.rootApiKey')}
                          </label>
                          <Input
                            id="setup-adminApiKey"
                            aria-describedby="setup-control-hint"
                            type="password"
                            value={draft.adminApiKey}
                            onChange={(event) =>
                              update({ adminApiKey: event.target.value })
                            }
                            className="h-11 rounded-lg"
                            {...PLAIN_INPUT_PROPS}
                            autoComplete="off"
                          />
                          <p
                            id="setup-control-hint"
                            className="text-xs font-normal text-muted-foreground"
                          >
                            {t('verifyDirectoryHint')}
                          </p>
                        </div>
                        {ADVANCED_FIELDS.map((key) => (
                          <label
                            className="grid gap-2 text-sm"
                            key={key}
                            htmlFor={`setup-${key}`}
                          >
                            {t(
                              `settings:fields.${key === 'accountId' ? 'account' : 'user'}`,
                            )}
                            <Input
                              id={`setup-${key}`}
                              className="focus-visible:ring-inset"
                              name={key}
                              type={'text'}
                              value={draft[key]}
                              onChange={(event) =>
                                update({ [key]: event.target.value })
                              }
                              {...PLAIN_INPUT_PROPS}
                              autoComplete={'off'}
                            />
                          </label>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              ) : null}
            </fieldset>
            {controlVerified && (
              <Button
                type="button"
                variant="ghost"
                aria-expanded={directoryOpen}
                aria-controls="setup-identity-options"
                onClick={() => setDirectoryOpen(!directoryOpen)}
              >
                {t('directoryOptions')}
              </Button>
            )}
            {controlVerified && directoryOpen && (
              <div id="setup-identity-options">
                <SetupIdentityDirectory
                  connection={draft}
                  disabled={busy}
                  onSelect={(identity) => {
                    const changed =
                      identity.accountId !== draft.accountId ||
                      identity.userId !== draft.userId
                    update({
                      ...identity,
                      ...(changed ? { apiKey: '' } : {}),
                    })
                    setSelectedDirectoryIdentity(true)
                  }}
                />
              </div>
            )}
            {error && error !== t('keyRequired') ? (
              <ErrorMessage
                error={error}
                className="rounded-lg border border-destructive/20 bg-destructive/5 p-3 leading-6"
              />
            ) : null}
            {verified ? (
              <p
                role="status"
                className="rounded-lg border border-emerald-600/20 bg-emerald-600/5 p-3 text-sm leading-6 text-emerald-700 dark:text-emerald-400"
              >
                {t(
                  studioSetupDestination(verified) === '/admin'
                    ? 'verifiedManagement'
                    : 'verified',
                )}
                {verified.accountId && verified.userId
                  ? ` ${verified.accountId} / ${verified.userId}`
                  : null}
              </p>
            ) : null}

            <Button type="submit" className="setup-submit" disabled={busy}>
              {busy ? (
                <Loader2Icon
                  aria-hidden="true"
                  className="size-4 animate-spin"
                />
              ) : null}
              {t(busy ? 'verifying' : verified ? 'enter' : 'connectAndEnter')}
              {!busy ? <img src={arrowRightIcon} alt="" /> : null}
            </Button>
          </form>
        </div>
      </section>
      <aside className="setup-visual">
        <div className="setup-visual-content">
          <img src={teamIllustration} alt="" width={567} height={378} />
          <p>{t('teamTagline')}</p>
        </div>
      </aside>
    </main>
  )
}
