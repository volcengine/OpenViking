import { fetchServerHealth } from '#/lib/connection/health'
import * as React from 'react'
import { createFileRoute, useNavigate } from '@tanstack/react-router'
import {
  CircleAlertIcon,
  CircleDashedIcon,
  CircleHelpIcon,
  ExternalLinkIcon,
  KeyRoundIcon,
  ShieldCheckIcon,
  TriangleAlertIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Alert, AlertDescription, AlertTitle } from '#/components/ui/alert'
import { Button } from '#/components/ui/button'
import { Badge } from '#/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '#/components/ui/card'
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldLabel,
} from '#/components/ui/field'
import { Input } from '#/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '#/components/ui/select'
import { canEditStudioConfiguration } from '#/lib/studio-permissions'
import { resolveConfiguredServiceProvider } from '#/lib/studio-service'
import { isValidConnectionUrl } from '#/lib/connection/profiles'
import { CLOUD_SETUP_URL } from '#/lib/connection/onboarding'
import { useStudioService } from '#/hooks/use-studio-service'
import { useAppConnection } from '#/hooks/use-app-connection'
import { useConnectionProbe } from '#/hooks/use-connection-probe'
import type { CapabilityProbeResult } from '#/lib/admin'
import { PLAIN_INPUT_PROPS } from '#/lib/form-input'
import { cn } from '#/lib/utils'
import { localizeCapabilityDetail } from './-lib/localize-capability-probe'
import type { ConnectionDraft } from '#/hooks/use-app-connection'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '#/components/ui/tabs'
import { ConnectionProfilesList } from './-components/connection-profiles'
import { ServerConfigurationEditor } from './-components/server-configuration'

export const Route = createFileRoute('/settings')({
  component: ConnectionSettingsRoute,
})

function ConnectionSettingsRoute() {
  const { t } = useTranslation('settings')
  const {
    identityScopeKey,
    isGatewayHost,
    activeProfileId,
    connection,
    connectionRole,
    isConnectionRoleLoading,
    serverMode,
  } = useAppConnection()
  const service = useStudioService()
  const canConfigure =
    service.ready &&
    canEditStudioConfiguration({
      serviceProvider: service.provider,
      hasControlCredential: Boolean(connection.adminApiKey.trim()),
      isRoleLoading: isConnectionRoleLoading,
      role: connectionRole,
      serverMode,
    })
  const [activeTab, setActiveTab] = React.useState('connection')
  React.useEffect(() => {
    setActiveTab('connection')
  }, [identityScopeKey, canConfigure])
  if (isGatewayHost) return <GatewayConnectionSettings />
  return (
    <div className="flex w-full min-w-0 flex-col gap-5">
      <h1 className="text-2xl font-semibold">{t('pageTitle')}</h1>
      <Tabs
        value={canConfigure ? activeTab : 'connection'}
        onValueChange={setActiveTab}
        className="min-w-0 gap-5"
      >
        <TabsList variant="line">
          <TabsTrigger value="connection">{t('tabs.connection')}</TabsTrigger>
          {canConfigure ? (
            <TabsTrigger value="configuration">
              {t('tabs.configuration')}
            </TabsTrigger>
          ) : null}
        </TabsList>
        <TabsContent value="connection">
          <div className="grid gap-5">
            <ConnectionProfilesList />
            <ConnectionSettingsPanel key={activeProfileId} />
          </div>
        </TabsContent>
        {canConfigure ? (
          <TabsContent value="configuration" keepMounted>
            <ServerConfigurationEditor key={identityScopeKey} />
          </TabsContent>
        ) : null}
      </Tabs>
    </div>
  )
}

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
  const [apiKey, setApiKey] = React.useState(
    connection.apiKey || connection.adminApiKey,
  )
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
      await navigate({ to: '/gateway' })
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

function getCapabilityIcon(result: CapabilityProbeResult | undefined) {
  if (!result) {
    return <CircleDashedIcon className="size-4" />
  }
  if (result.state === 'ok') {
    return <ShieldCheckIcon className="size-4" />
  }
  if (result.state === 'error') {
    return <CircleAlertIcon className="size-4" />
  }
  return <CircleDashedIcon className="size-4" />
}

function CapabilityStatus({
  isLoading,
  label,
  result,
}: {
  isLoading: boolean
  label: string
  result: CapabilityProbeResult | undefined
}) {
  const { t } = useTranslation('settings')
  const state = isLoading ? 'checking' : result?.state || 'skipped'
  const detail = localizeCapabilityDetail(result, t)

  return (
    <div
      className={cn(
        'flex min-w-0 items-start gap-2 rounded-md border bg-background/70 px-3 py-2 text-sm',
        state === 'ok' && 'border-emerald-500/35 text-emerald-700',
        state === 'error' && 'border-destructive/35 text-destructive',
      )}
    >
      <div className={cn('mt-0.5', isLoading && 'animate-spin')}>
        {getCapabilityIcon(result)}
      </div>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="font-medium">{label}</span>
          <span className="text-xs text-muted-foreground">
            {t(`health.state.${state}`)}
          </span>
        </div>
        {detail ? (
          <p className="mt-1 truncate text-xs text-muted-foreground">
            {detail}
          </p>
        ) : null}
      </div>
    </div>
  )
}

function UserApiKeyInput({
  identity,
  id,
  onChange,
  placeholder,
  value,
}: {
  identity: string
  id: string
  onChange: (value: string) => void
  placeholder: string
  value: string
}) {
  return (
    <div className="flex h-9 w-full min-w-0 items-center gap-2 rounded-md border border-input bg-transparent bg-clip-padding px-2.5 shadow-xs transition-[color,box-shadow] focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/50 dark:bg-input/30">
      <span className="shrink-0 rounded-sm bg-muted px-2 py-1 font-mono text-xs text-muted-foreground">
        [{identity}]
      </span>
      <input
        id={id}
        type="password"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        className="h-full min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
        {...PLAIN_INPUT_PROPS}
      />
    </div>
  )
}

function ConnectionSettingsPanel() {
  const { i18n, t } = useTranslation(['settings', 'studio'])
  const service = useStudioService()
  const { connection, reconnect, saveConnection, serverMode, serverHealth } =
    useAppConnection()
  const [draft, setDraft] = React.useState<ConnectionDraft>(connection)
  const previousConnectionRef = React.useRef({ ...connection })
  const openSourceUrlRef = React.useRef(
    resolveConfiguredServiceProvider(
      connection.baseUrl,
      connection.serviceSelection,
    ) === 'opensource'
      ? connection.baseUrl
      : '',
  )
  const [saveError, setSaveError] = React.useState(false)
  const canEditControlCredential =
    resolveConfiguredServiceProvider(draft.baseUrl, draft.serviceSelection) ===
    'opensource'
  React.useEffect(() => {
    const previous = previousConnectionRef.current
    previousConnectionRef.current = { ...connection }
    if (
      previous.baseUrl !== connection.baseUrl ||
      previous.serviceSelection !== connection.serviceSelection ||
      previous.apiKey !== connection.apiKey ||
      previous.adminApiKey !== connection.adminApiKey
    ) {
      if (
        resolveConfiguredServiceProvider(
          connection.baseUrl,
          connection.serviceSelection,
        ) === 'opensource'
      ) {
        openSourceUrlRef.current = connection.baseUrl
      }
      setDraft(connection)
      setSaveError(false)
    } else if (
      previous.accountId !== connection.accountId ||
      previous.userId !== connection.userId
    ) {
      // Health may fill in identity while the user edits a recovery address/key.
      // Update untouched identity fields without replacing the in-progress form.
      setDraft((current) => ({
        ...current,
        accountId:
          current.accountId === previous.accountId
            ? connection.accountId
            : current.accountId,
        userId:
          current.userId === previous.userId
            ? connection.userId
            : current.userId,
      }))
    }
  }, [connection])

  function updateDraft(next: Partial<ConnectionDraft>): void {
    setSaveError(false)
    setDraft((current) => ({ ...current, ...next }))
  }

  function selectServiceType(selection: 'opensource' | 'volcengine'): void {
    if (
      resolveConfiguredServiceProvider(
        draft.baseUrl,
        draft.serviceSelection,
      ) === 'opensource' &&
      selection === 'volcengine'
    ) {
      openSourceUrlRef.current = draft.baseUrl
    }
    updateDraft({
      serviceSelection: selection,
      baseUrl:
        selection === 'volcengine' ? CLOUD_SETUP_URL : openSourceUrlRef.current,
    })
  }

  const resolvedIdentity =
    draft.baseUrl === connection.baseUrl &&
    draft.apiKey === connection.apiKey &&
    draft.serviceSelection === connection.serviceSelection &&
    (draft.apiKey || draft.adminApiKey === connection.adminApiKey) &&
    typeof serverHealth?.account_id === 'string' &&
    serverHealth.account_id &&
    typeof serverHealth.user_id === 'string' &&
    serverHealth.user_id
      ? `${serverHealth.account_id}/${serverHealth.user_id}`
      : t('studio:profiles.identityPending')

  const probeQuery = useConnectionProbe()
  const isDevMode = serverMode === 'dev'
  const isUnsupportedAuthMode = serverMode === 'oidc' || serverMode === 'ldap'
  const rootApiKey = connection.adminApiKey.trim()
  const hasControlCredential =
    service.provider === 'opensource' && Boolean(draft.adminApiKey.trim())
  const hasDataCredential = Boolean(draft.apiKey.trim())
  const adminProbe = probeQuery.data?.admin
  const dataProbe = probeQuery.data?.data
  const hasAdminAccess = !isDevMode && adminProbe?.state === 'ok'
  const authenticationGuideUrl = i18n.resolvedLanguage?.startsWith('zh')
    ? 'https://docs.openviking.ai/zh/guides/04-authentication'
    : 'https://docs.openviking.ai/en/guides/04-authentication'
  const trustedCredentialRequired =
    serverMode === 'trusted' && !probeQuery.isFetching && !hasControlCredential
  const keyGuide =
    service.provider !== 'opensource'
      ? null
      : serverMode === 'trusted'
        ? trustedCredentialRequired
          ? {
              primary: t('connection.keyGuide.trusted.primary'),
              secondary: t('connection.keyGuide.trusted.secondary'),
              title: t('connection.keyGuide.trusted.title'),
            }
          : null
        : !hasControlCredential && !hasDataCredential
          ? {
              primary: t('connection.keyGuide.empty.primary'),
              secondary: t('connection.keyGuide.empty.secondary'),
              title: t('connection.keyGuide.empty.title'),
            }
          : !hasControlCredential
            ? {
                primary: t('connection.keyGuide.control.primary'),
                secondary: t('connection.keyGuide.control.secondary'),
                title: t('connection.keyGuide.control.title'),
              }
            : !hasDataCredential
              ? {
                  primary: t('connection.keyGuide.data.primary'),
                  secondary: t('connection.keyGuide.data.secondary'),
                  title: t('connection.keyGuide.data.title'),
                }
              : null

  return (
    <div className="flex w-full min-w-0 flex-col gap-5">
      <Card className="gap-0 overflow-hidden border-primary/25 bg-primary/[0.025] py-0 shadow-sm ring-1 ring-primary/10">
        <CardHeader className="gap-2 border-b border-primary/15 bg-primary/[0.07] px-5 py-3.5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex min-w-0 items-center gap-2">
              <div className="flex size-7 items-center justify-center rounded-md bg-primary text-primary-foreground">
                <KeyRoundIcon className="size-4" />
              </div>
              <CardTitle>{t('connection.title')}</CardTitle>
            </div>
            <Badge variant="outline" className="shrink-0 font-normal">
              {t(`serverMode.${serverMode}`)}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="grid gap-4 px-5 py-4">
          <Field>
            <FieldLabel htmlFor="settings-service-type">
              {t('studio:serviceType')}
            </FieldLabel>
            <FieldContent>
              <Select
                value={
                  draft.serviceSelection === 'auto'
                    ? resolveConfiguredServiceProvider(draft.baseUrl)
                    : draft.serviceSelection
                }
                onValueChange={(value) =>
                  selectServiceType(value as 'opensource' | 'volcengine')
                }
              >
                <SelectTrigger
                  id="settings-service-type"
                  className="w-full bg-background"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent
                  align="start"
                  className="w-[var(--anchor-width)]"
                >
                  <SelectItem value="opensource">
                    {t('studio:providers.opensource')}
                  </SelectItem>
                  <SelectItem value="volcengine">
                    {t('studio:providers.volcengine')}
                  </SelectItem>
                </SelectContent>
              </Select>
              <FieldDescription>
                {t('studio:profiles.typeHint')}
              </FieldDescription>
              <Button
                type="button"
                variant="outline"
                disabled={service.isChecking}
                onClick={reconnect}
              >
                {t('studio:retry')}
              </Button>
              <p className="text-sm" aria-live="polite">
                {t(
                  `studio:providers.${service.isChecking ? 'checking' : service.provider}`,
                )}{' '}
                {service.version}
              </p>
              <FieldDescription>
                {t(`studio:connectionSummary.${service.provider}`)}
              </FieldDescription>
            </FieldContent>
          </Field>
          <Field>
            <FieldLabel htmlFor="settings-base-url">
              {t('fields.baseUrl')}
            </FieldLabel>
            <FieldContent>
              <Input
                id="settings-base-url"
                value={draft.baseUrl}
                onChange={(event) =>
                  updateDraft({ baseUrl: event.target.value })
                }
                readOnly={
                  resolveConfiguredServiceProvider(
                    draft.baseUrl,
                    draft.serviceSelection,
                  ) === 'volcengine'
                }
                placeholder={t('placeholders.baseUrl')}
                inputMode="url"
                {...PLAIN_INPUT_PROPS}
              />
            </FieldContent>
          </Field>

          {isDevMode ? (
            <p className="rounded-md border border-dashed bg-muted/30 px-3 py-2 text-sm text-muted-foreground">
              {t('connection.devMode')}
            </p>
          ) : isUnsupportedAuthMode ? (
            <Alert variant="destructive" className="border-destructive/50">
              <TriangleAlertIcon className="size-5" />
              <AlertTitle>
                {t('connection.unsupportedAuthMode.title')}
              </AlertTitle>
              <AlertDescription className="grid gap-2">
                <p>
                  {t('connection.unsupportedAuthMode.primary', {
                    mode: serverMode,
                  })}
                </p>
                <p>
                  {t('connection.unsupportedAuthMode.description', {
                    mode: serverMode,
                    ov: 'ov',
                  })}
                </p>
                <a
                  href={authenticationGuideUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-1 inline-flex w-fit items-center gap-1 font-medium text-foreground underline underline-offset-2"
                >
                  {t('connection.keyGuide.learnMore')}
                  <ExternalLinkIcon className="size-3.5" />
                </a>
              </AlertDescription>
            </Alert>
          ) : (
            <>
              <div className="grid gap-4 md:grid-cols-2">
                {canEditControlCredential ? (
                  <Field>
                    <FieldLabel htmlFor="settings-root-api-key">
                      {t('fields.rootApiKey')}
                    </FieldLabel>
                    <FieldContent>
                      <Input
                        id="settings-root-api-key"
                        type="password"
                        value={draft.adminApiKey}
                        onChange={(event) =>
                          updateDraft({ adminApiKey: event.target.value })
                        }
                        placeholder={t('placeholders.adminApiKey')}
                        {...PLAIN_INPUT_PROPS}
                      />
                      <FieldDescription>
                        {t(
                          serverMode === 'trusted'
                            ? 'connection.trustedRootHint'
                            : 'connection.rootHint',
                        )}
                      </FieldDescription>
                    </FieldContent>
                  </Field>
                ) : null}
                <Field>
                  <FieldLabel htmlFor="settings-user-api-key">
                    {t('fields.userApiKey')}
                  </FieldLabel>
                  <FieldContent>
                    <UserApiKeyInput
                      identity={resolvedIdentity}
                      id="settings-user-api-key"
                      value={draft.apiKey}
                      onChange={(apiKey) => updateDraft({ apiKey })}
                      placeholder={t('placeholders.userApiKey')}
                    />
                    <FieldDescription>
                      {t(
                        serverMode === 'trusted'
                          ? 'connection.trustedUserHint'
                          : 'connection.userHint',
                      )}
                    </FieldDescription>
                  </FieldContent>
                </Field>
              </div>

              <div className="grid gap-2 md:grid-cols-2">
                {service.provider === 'opensource' ? (
                  <CapabilityStatus
                    isLoading={probeQuery.isFetching}
                    label={t('health.admin')}
                    result={adminProbe}
                  />
                ) : null}
                <CapabilityStatus
                  isLoading={probeQuery.isFetching}
                  label={t('health.data')}
                  result={dataProbe}
                />
              </div>

              {service.provider === 'volcengine' ? (
                <p className="text-sm text-muted-foreground">
                  {t('studio:cloudManagement')}
                </p>
              ) : null}
              {(serverMode === 'api_key' || serverMode === 'trusted') &&
              keyGuide ? (
                <Alert className="border-primary/25 bg-primary/[0.045]">
                  <CircleHelpIcon className="text-primary" />
                  <AlertTitle>{keyGuide.title}</AlertTitle>
                  <AlertDescription className="grid gap-1.5 text-pretty [&_p:not(:last-child)]:mb-0">
                    <p>{keyGuide.primary}</p>
                    <p>{keyGuide.secondary}</p>
                    <a
                      href={authenticationGuideUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="mt-1 inline-flex w-fit items-center gap-1 font-medium text-foreground"
                    >
                      {t('connection.keyGuide.learnMore')}
                      <ExternalLinkIcon className="size-3.5" />
                    </a>
                  </AlertDescription>
                </Alert>
              ) : null}

              {!hasAdminAccess &&
              rootApiKey &&
              adminProbe?.state === 'error' ? (
                <p className="text-sm text-destructive">
                  {t('connection.adminError', {
                    message: adminProbe.detail || '',
                  })}
                </p>
              ) : null}
            </>
          )}
          {saveError && (
            <p role="alert" className="text-sm text-destructive">
              {t('studio:profiles.invalidUrl')}
            </p>
          )}
          <div className="flex justify-end">
            <Button
              onClick={() => {
                if (!isValidConnectionUrl(draft.baseUrl)) {
                  setSaveError(true)
                  return
                }
                saveConnection(draft)
                reconnect()
              }}
              disabled={JSON.stringify(draft) === JSON.stringify(connection)}
            >
              {t('studio:profiles.save')}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
