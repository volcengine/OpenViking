import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useTheme } from 'next-themes'
import {
  ArrowRightIcon,
  ArrowLeftIcon,
  CheckIcon,
  ChevronDownIcon,
  CloudIcon,
  ServerIcon,
  Loader2Icon,
} from 'lucide-react'
import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '#/components/ui/select'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '#/components/ui/card'
import { PLAIN_INPUT_PROPS } from '#/lib/form-input'
import {
  StudioSetupError,
  StudioSetupRequestError,
  studioSetupDestination,
  CLOUD_SETUP_URL,
  completeStudioSetup,
  markStudioSetupPending,
  savedSetupConnections,
  verifyStudioSetup,
} from '#/lib/connection/onboarding'
import { isValidConnectionUrl } from '#/lib/connection/profiles'
import type { ConnectionDraft } from '#/hooks/use-app-connection'

const LANGUAGES = [
  ['zh-CN', '中文'],
  ['en', 'English'],
] as const
const PROVIDERS = [
  ['opensource', ServerIcon],
  ['volcengine', CloudIcon],
] as const
const THEMES = ['light', 'dark', 'system'] as const
const ADVANCED_FIELDS = ['adminApiKey', 'accountId', 'userId'] as const
const NEW_CONNECTION = 'new'
type SavedConnection = ReturnType<typeof savedSetupConnections>[number]

function withoutCredentials(connection: ConnectionDraft): ConnectionDraft {
  return { ...connection, apiKey: '', adminApiKey: '' }
}

function savedConnectionTitle(profile: SavedConnection): string {
  const identity = [
    profile.connection.accountId,
    profile.connection.userId,
  ].filter((value) => value && value !== 'default')
  return identity.length
    ? `${profile.name} · ${identity.join('/')}`
    : profile.name
}

function savedConnectionLabel(
  profile: SavedConnection,
  profiles: SavedConnection[],
): string {
  const title = savedConnectionTitle(profile)
  const sameLabel = profiles.filter(
    (candidate) =>
      savedConnectionTitle(candidate) === title &&
      candidate.connection.baseUrl === profile.connection.baseUrl,
  )
  if (sameLabel.length < 2) return title
  return `${title} (${sameLabel.findIndex((candidate) => candidate.id === profile.id) + 1})`
}

function newConnectionDraft(
  provider: (typeof PROVIDERS)[number][0],
): ConnectionDraft {
  return {
    serviceSelection: provider,
    baseUrl: provider === 'volcengine' ? CLOUD_SETUP_URL : '',
    apiKey: '',
    adminApiKey: '',
    accountId: 'default',
    userId: 'default',
  }
}

export function StudioSetup({
  onComplete,
}: {
  onComplete: (connection: ConnectionDraft) => void
}) {
  const { t, i18n } = useTranslation(['onboarding', 'studio', 'settings'])
  const { theme, setTheme } = useTheme()
  const [step, setStep] = useState(0)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [activeProfileId] = useState(() => {
    try {
      return JSON.parse(
        localStorage.getItem('ov_console_connection_profiles') || '{}',
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
        const identity = JSON.stringify([
          connection.serviceSelection,
          address,
          connection.accountId,
          connection.userId,
          connection.apiKey,
          connection.adminApiKey,
        ])
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
  const needsNewAddress =
    draft.serviceSelection === 'opensource' &&
    selectedConnectionId === NEW_CONNECTION
  const canContinue =
    (draft.serviceSelection === 'opensource' && !needsNewAddress) ||
    isValidConnectionUrl(draft.baseUrl)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const connectionRevisionRef = useRef(0)
  const verifiedSelectionRef = useRef<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const keyInputRef = useRef<HTMLInputElement>(null)
  const headingRef = useRef<HTMLHeadingElement>(null)
  const cardRef = useRef<HTMLDivElement>(null)
  const cardAnimationRef = useRef<Animation | null>(null)
  const previousCardHeightRef = useRef<number | null>(null)
  useEffect(() => {
    markStudioSetupPending()
    return () => abortRef.current?.abort()
  }, [])
  useEffect(() => {
    headingRef.current?.focus()
  }, [step])
  useLayoutEffect(() => {
    const card = cardRef.current
    const previousHeight = previousCardHeightRef.current
    previousCardHeightRef.current = null
    if (!card || previousHeight === null) return

    const nextHeight = card.getBoundingClientRect().height
    if (
      previousHeight === nextHeight ||
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    )
      return

    cardAnimationRef.current = card.animate(
      [{ height: `${previousHeight}px` }, { height: `${nextHeight}px` }],
      { duration: 360, easing: 'cubic-bezier(0.22, 1, 0.36, 1)' },
    )
    cardAnimationRef.current.onfinish = () => {
      cardAnimationRef.current = null
    }
  }, [step])
  function changeStep(nextStep: number) {
    previousCardHeightRef.current =
      cardRef.current?.getBoundingClientRect().height ?? null
    cardAnimationRef.current?.cancel()
    cardAnimationRef.current = null
    setStep(nextStep)
  }
  const language = (i18n.resolvedLanguage ?? i18n.language).startsWith('zh')
    ? 'zh-CN'
    : 'en'
  function update(next: Partial<ConnectionDraft>) {
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
      try {
        const matchingHostedProfile =
          verified.serviceSelection === 'volcengine'
            ? savedSetupConnections().find(
                (profile) =>
                  profile.connection.serviceSelection === 'volcengine' &&
                  new URL(profile.connection.baseUrl).href.replace(
                    /\/+$/,
                    '',
                  ) === new URL(verified.baseUrl).href.replace(/\/+$/, '') &&
                  typeof profile.connection.apiKey === 'string' &&
                  profile.connection.apiKey.trim() === verified.apiKey,
              )
            : undefined
        completeStudioSetup(
          verified,
          t(`studio:providers.${verified.serviceSelection}`),
          verified.serviceSelection === 'volcengine'
            ? matchingHostedProfile?.id
            : selectedConnectionId === NEW_CONNECTION
              ? undefined
              : selectedConnectionId,
        )
        onComplete(verified)
      } catch {
        setError(t('storageError'))
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
        setDraft(result)
        verifiedSelectionRef.current = selectedConnectionId
        setVerified(result)
      }
    } catch (err) {
      if (!controller.signal.aborted)
        setError(
          err instanceof StudioSetupRequestError
            ? `${t(err.stage === 'health' ? 'healthRequestFailed' : 'dataRequestFailed')} ${err.message}`
            : err instanceof StudioSetupError
              ? t(err.code)
              : err instanceof Error
                ? err.message
                : String(err),
        )
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null
        setBusy(false)
      }
    }
  }
  return (
    <main className="flex h-svh flex-col items-center overflow-y-auto bg-background px-5 py-6 text-foreground sm:px-8 sm:py-8 lg:pt-[max(2rem,calc((100svh-52rem)/2))]">
      <div className="w-full max-w-5xl shrink-0">
        <div className="mb-6 flex items-center gap-3 lg:mb-8">
          <img
            src={`${import.meta.env.BASE_URL}ov-logo.png`}
            alt=""
            className="size-10 rounded-xl"
          />
          <span className="text-base font-semibold tracking-tight">
            {t('brand')}
          </span>
        </div>
        <div className="grid items-start gap-6 lg:grid-cols-[1fr_1.15fr] lg:gap-20">
          <header className="lg:pt-5">
            <h1
              ref={headingRef}
              tabIndex={-1}
              className="whitespace-nowrap text-[clamp(1.25rem,3vw,2rem)] leading-tight font-semibold tracking-tight outline-none"
            >
              {step === 0 ? t('workspace') : t('connectionTitle')}
            </h1>
            <p className="mt-3 max-w-sm text-sm leading-6 text-muted-foreground lg:mt-5 lg:min-h-14 lg:leading-7">
              {step === 0
                ? t('welcomeDescription')
                : t('connectionDescription')}
            </p>
            <ol
              className="mt-5 flex gap-6 lg:mt-12 lg:flex-col lg:gap-7"
              aria-label={t('steps')}
            >
              {[t('workspace'), t('connection')].map((label, index) => (
                <li
                  key={index}
                  aria-current={step === index ? 'step' : undefined}
                  className="relative flex items-start gap-3"
                >
                  {index === 0 ? (
                    <span
                      aria-hidden="true"
                      className="absolute left-[11.5px] top-7 hidden h-[calc(100%-4px)] w-px overflow-hidden rounded-full bg-border/70 lg:block"
                    >
                      <span
                        className={`block h-full w-full origin-top bg-foreground/70 transition-transform duration-500 ease-out motion-reduce:transition-none ${step === 1 ? 'scale-y-100' : 'scale-y-0'}`}
                      />
                    </span>
                  ) : null}
                  <span
                    aria-hidden="true"
                    className={`relative z-10 flex size-6 shrink-0 items-center justify-center rounded-full border text-[11px] font-medium transition-colors duration-300 motion-reduce:transition-none ${step >= index ? 'border-foreground bg-foreground text-background' : 'border-border text-muted-foreground'}`}
                  >
                    {step > index ? (
                      <CheckIcon className="size-3" />
                    ) : (
                      index + 1
                    )}
                  </span>
                  <div className="pt-1">
                    <p
                      className={`text-sm font-medium ${step === index ? 'text-foreground' : 'text-muted-foreground'}`}
                    >
                      {label}
                    </p>
                    <p className="mt-1 hidden text-xs leading-5 text-muted-foreground lg:block">
                      {t(index === 0 ? 'workspaceHint' : 'connectionHint')}
                    </p>
                  </div>
                </li>
              ))}
            </ol>
          </header>
          <Card
            ref={cardRef}
            className="gap-0 overflow-hidden rounded-2xl border-border/70 bg-card py-0 shadow-sm"
          >
            <CardHeader className="gap-2 border-b border-border/70 px-6 py-4 sm:px-8 sm:py-5">
              <CardTitle className="text-lg font-semibold tracking-tight">
                {step === 0 ? t('workspace') : t('connection')}
              </CardTitle>
              <CardDescription className="text-sm leading-6">
                {t(step === 0 ? 'workspaceHint' : 'connectionHint')}
              </CardDescription>
            </CardHeader>
            <CardContent className="px-6 py-5 sm:px-8">
              {step === 0 ? (
                <div className="space-y-4">
                  <fieldset className="space-y-3">
                    <legend className="text-sm font-medium">
                      {t('studio:serviceType')}
                    </legend>
                    <div className="grid grid-cols-2 gap-3">
                      {PROVIDERS.map(([provider, Icon]) => (
                        <label
                          key={provider}
                          className="flex min-h-16 cursor-pointer items-center gap-2.5 rounded-lg border border-border px-3 text-sm font-medium transition-colors hover:bg-muted/40 has-checked:border-foreground has-checked:bg-muted/40 has-focus-visible:ring-2 has-focus-visible:ring-ring"
                        >
                          <input
                            className="sr-only"
                            type="radio"
                            name="provider"
                            value={provider}
                            checked={draft.serviceSelection === provider}
                            onChange={() => switchProvider(provider)}
                          />
                          <Icon
                            aria-hidden="true"
                            className="size-4 shrink-0 text-muted-foreground"
                          />
                          {t(`studio:providers.${provider}`)}
                        </label>
                      ))}
                    </div>
                  </fieldset>
                  {draft.serviceSelection === 'opensource' ? (
                    <div className="space-y-2">
                      <p className="text-sm font-medium">
                        {t('connectionChoice')}
                      </p>
                      <Select
                        value={selectedConnectionId}
                        onValueChange={(value) => {
                          if (value) selectConnection(value)
                        }}
                      >
                        <SelectTrigger
                          aria-label={t('connectionChoice')}
                          className="h-11 w-full min-w-0 rounded-lg bg-background px-4 shadow-xs"
                        >
                          <SelectValue>
                            {selectedProfile
                              ? savedConnectionLabel(
                                  selectedProfile,
                                  providerConnections,
                                )
                              : t('newConnection')}
                          </SelectValue>
                        </SelectTrigger>
                        <SelectContent
                          align="start"
                          className="w-[var(--anchor-width)] max-w-[calc(100vw-2rem)] rounded-xl border border-border bg-popover p-1 shadow-lg before:hidden"
                        >
                          {providerConnections.map((profile) => (
                            <SelectItem
                              key={profile.id}
                              value={profile.id}
                              className="min-h-10 overflow-hidden rounded-lg px-3 pr-9"
                              title={profile.connection.baseUrl}
                            >
                              <span className="block min-w-0 truncate font-medium">
                                {savedConnectionLabel(
                                  profile,
                                  providerConnections,
                                )}
                              </span>
                            </SelectItem>
                          ))}
                          <SelectItem
                            value={NEW_CONNECTION}
                            className="min-h-10 rounded-lg px-3 pr-9"
                          >
                            {t('newConnection')}
                          </SelectItem>
                        </SelectContent>
                      </Select>
                      {needsNewAddress ? (
                        <div className="space-y-2 pt-2">
                          <label htmlFor="setup-new-url" className="sr-only">
                            {t('settings:fields.baseUrl')}
                          </label>
                          <Input
                            id="setup-new-url"
                            className="h-11 rounded-lg"
                            type="url"
                            value={draft.baseUrl}
                            placeholder="http://127.0.0.1:1933"
                            aria-invalid={
                              Boolean(draft.baseUrl) && !canContinue
                            }
                            aria-describedby="setup-new-url-help"
                            onChange={(event) =>
                              update({ baseUrl: event.target.value })
                            }
                            {...PLAIN_INPUT_PROPS}
                          />
                          <p
                            id="setup-new-url-help"
                            className="text-xs text-muted-foreground"
                          >
                            {draft.baseUrl && !canContinue
                              ? t('studio:profiles.invalidUrl')
                              : t('newAddressHint')}
                          </p>
                        </div>
                      ) : (
                        <p
                          className="line-clamp-2 break-all px-1 pt-1 text-xs text-muted-foreground"
                          title={draft.baseUrl}
                        >
                          {draft.baseUrl}
                        </p>
                      )}
                    </div>
                  ) : (
                    <div className="space-y-2">
                      <label
                        htmlFor="setup-hosted-url"
                        className="text-sm font-medium"
                      >
                        {t('hostedEndpoint')}
                      </label>
                      <Input
                        id="setup-hosted-url"
                        className="h-11 rounded-lg"
                        type="url"
                        value={draft.baseUrl}
                        aria-invalid={Boolean(draft.baseUrl) && !canContinue}
                        onChange={(event) =>
                          update({
                            baseUrl: event.target.value,
                            apiKey: '',
                            adminApiKey: '',
                          })
                        }
                        {...PLAIN_INPUT_PROPS}
                      />
                      {draft.baseUrl && !canContinue ? (
                        <p className="text-xs text-destructive">
                          {t('studio:profiles.invalidUrl')}
                        </p>
                      ) : null}
                    </div>
                  )}
                  <fieldset className="space-y-3">
                    <legend className="text-sm font-medium">
                      {t('language')}
                    </legend>
                    <div className="grid grid-cols-2 gap-3">
                      {LANGUAGES.map(([value, label]) => (
                        <label
                          key={value}
                          className="flex min-h-11 cursor-pointer items-center justify-between gap-3 rounded-lg border border-border px-4 transition-colors hover:bg-muted/40 has-checked:border-foreground has-checked:bg-muted/40 has-focus-visible:ring-2 has-focus-visible:ring-ring has-focus-visible:ring-offset-2"
                        >
                          <input
                            className="sr-only"
                            type="radio"
                            name="language"
                            value={value}
                            checked={language === value}
                            onChange={() => {
                              void i18n.changeLanguage(value)
                              document.documentElement.lang = value
                            }}
                          />
                          <span className="text-sm font-medium">{label}</span>
                          <span
                            aria-hidden="true"
                            className={`flex size-4 items-center justify-center rounded-full border ${language === value ? 'border-foreground bg-foreground text-background' : 'border-muted-foreground/30'}`}
                          >
                            {language === value ? (
                              <CheckIcon className="size-3" />
                            ) : null}
                          </span>
                        </label>
                      ))}
                    </div>
                  </fieldset>
                  <fieldset className="space-y-3">
                    <legend className="text-sm font-medium">
                      {t('theme')}
                    </legend>
                    <div className="grid grid-cols-3 gap-3">
                      {THEMES.map((value) => (
                        <label
                          key={value}
                          className="min-w-0 cursor-pointer rounded-xl border border-border p-2 transition-colors hover:bg-muted/40 has-checked:border-foreground has-checked:bg-muted/30 has-focus-visible:ring-2 has-focus-visible:ring-ring has-focus-visible:ring-offset-2"
                        >
                          <input
                            className="sr-only"
                            type="radio"
                            name="theme"
                            value={value}
                            checked={(theme ?? 'system') === value}
                            onChange={() => setTheme(value)}
                          />
                          <ThemePreview appearance={value} />
                          <span className="mt-2 flex items-center justify-between gap-1 text-xs font-medium sm:text-sm">
                            {t(value)}
                            {(theme ?? 'system') === value ? (
                              <CheckIcon
                                aria-hidden="true"
                                className="size-3.5 shrink-0"
                              />
                            ) : null}
                          </span>
                        </label>
                      ))}
                    </div>
                  </fieldset>
                  <Button
                    className="mt-2 min-h-11 w-full rounded-lg"
                    disabled={!canContinue}
                    onClick={() => changeStep(1)}
                  >
                    {t('next')}
                    <ArrowRightIcon aria-hidden="true" className="size-4" />
                  </Button>
                </div>
              ) : (
                <form
                  onSubmit={(event) => void submit(event)}
                  className="space-y-5"
                >
                  <fieldset disabled={busy} className="space-y-4">
                    <div className="grid gap-2 text-sm">
                      <span className="font-medium">
                        {t('settings:fields.baseUrl')}
                      </span>
                      <p className="break-all rounded-lg border border-border bg-muted/30 px-4 py-3 text-sm">
                        {draft.baseUrl}
                      </p>
                    </div>
                    <label
                      className="grid gap-2 text-sm font-medium"
                      htmlFor="setup-key"
                    >
                      {t('settings:fields.userApiKey')}
                      <Input
                        ref={keyInputRef}
                        className="h-11 rounded-lg bg-background/60 text-sm"
                        id="setup-key"
                        name="apiKey"
                        type="password"
                        aria-invalid={error === t('keyRequired')}
                        aria-describedby={
                          error === t('keyRequired')
                            ? 'setup-key-error'
                            : undefined
                        }
                        value={draft.apiKey}
                        onChange={(event) =>
                          update({ apiKey: event.target.value })
                        }
                        {...PLAIN_INPUT_PROPS}
                        autoComplete="new-password"
                      />
                    </label>
                    {error === t('keyRequired') ? (
                      <p
                        id="setup-key-error"
                        role="alert"
                        className="text-sm text-destructive"
                      >
                        {error}
                      </p>
                    ) : null}
                    <p className="text-sm text-muted-foreground">
                      {t(
                        draft.serviceSelection === 'volcengine'
                          ? 'hostedKeyHelp'
                          : 'keyHelp',
                      )}
                    </p>
                    {draft.serviceSelection === 'opensource' ? (
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
                              {ADVANCED_FIELDS.map((key) => (
                                <label
                                  className="grid gap-2 text-sm"
                                  key={key}
                                  htmlFor={`setup-${key}`}
                                >
                                  {t(
                                    `settings:fields.${key === 'accountId' ? 'account' : key === 'userId' ? 'user' : 'rootApiKey'}`,
                                  )}
                                  <Input
                                    id={`setup-${key}`}
                                    className="focus-visible:ring-inset"
                                    name={key}
                                    type={
                                      key === 'adminApiKey'
                                        ? 'password'
                                        : 'text'
                                    }
                                    value={draft[key]}
                                    onChange={(event) =>
                                      update({ [key]: event.target.value })
                                    }
                                    {...PLAIN_INPUT_PROPS}
                                    autoComplete={
                                      key === 'adminApiKey'
                                        ? 'new-password'
                                        : 'off'
                                    }
                                  />
                                </label>
                              ))}
                            </div>
                          </div>
                        </div>
                      </div>
                    ) : null}
                  </fieldset>
                  {error && error !== t('keyRequired') ? (
                    <p
                      role="alert"
                      className="rounded-lg border border-destructive/20 bg-destructive/5 p-3 text-sm leading-6 text-destructive"
                    >
                      {t('verifyFailed')} {error}
                    </p>
                  ) : null}
                  {verified ? (
                    <p
                      role="status"
                      className="rounded-lg border border-emerald-600/20 bg-emerald-600/5 p-3 text-sm leading-6 text-emerald-700 dark:text-emerald-400"
                    >
                      {t(
                        studioSetupDestination(verified) === '/users'
                          ? 'verifiedManagement'
                          : 'verified',
                      )}
                      {verified.accountId && verified.userId
                        ? ` ${verified.accountId} / ${verified.userId}`
                        : null}
                    </p>
                  ) : null}
                  <div className="flex gap-3 border-t border-border/70 pt-5">
                    <Button
                      type="button"
                      variant="outline"
                      className="min-h-11 rounded-lg"
                      onClick={() => {
                        abortRef.current?.abort()
                        setBusy(false)
                        changeStep(0)
                      }}
                    >
                      <ArrowLeftIcon aria-hidden="true" className="size-4" />
                      {t('back')}
                    </Button>
                    <Button
                      type="submit"
                      className="min-h-11 flex-1 rounded-lg"
                      disabled={busy || !isValidConnectionUrl(draft.baseUrl)}
                    >
                      {busy ? (
                        <Loader2Icon className="size-4 animate-spin" />
                      ) : null}
                      {t(busy ? 'verifying' : verified ? 'enter' : 'verify')}
                    </Button>
                  </div>
                </form>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </main>
  )
}

function ThemePreview({ appearance }: { appearance: (typeof THEMES)[number] }) {
  return (
    <div
      aria-hidden="true"
      className={`relative h-14 overflow-hidden rounded-md border border-black/10 ${appearance === 'light' ? 'bg-[#f9fafb]' : appearance === 'dark' ? 'bg-[#191b20]' : 'bg-[linear-gradient(110deg,#f9fafb_50%,#191b20_50%)]'}`}
    >
      <div
        className={`flex h-4 items-center gap-1 border-b px-2 ${appearance === 'dark' ? 'border-white/10' : 'border-black/10'}`}
      >
        {[0, 1, 2].map((dot) => (
          <span
            key={dot}
            className={`size-1 rounded-full ${appearance === 'dark' ? 'bg-white/25' : 'bg-black/20'}`}
          />
        ))}
      </div>
      <div className="flex h-full">
        <div
          className={`w-1/4 border-r p-1.5 ${appearance === 'dark' ? 'border-white/10 bg-white/5' : 'border-black/10 bg-black/[0.03]'}`}
        >
          <div
            className={`mb-1.5 h-1 rounded-full ${appearance === 'dark' ? 'bg-white/20' : 'bg-black/15'}`}
          />
          <div
            className={`h-1 w-2/3 rounded-full ${appearance === 'dark' ? 'bg-white/10' : 'bg-black/10'}`}
          />
        </div>
        <div className="flex-1 space-y-1.5 p-2">
          <div
            className={`h-1.5 w-3/5 rounded-full ${appearance === 'dark' ? 'bg-white/35' : 'bg-black/20'}`}
          />
          <div
            className={`h-1 w-full rounded-full ${appearance === 'dark' ? 'bg-white/10' : 'bg-black/10'}`}
          />
          <div
            className={`h-1 w-4/5 rounded-full ${appearance === 'dark' ? 'bg-white/10' : 'bg-black/10'}`}
          />
        </div>
      </div>
    </div>
  )
}
