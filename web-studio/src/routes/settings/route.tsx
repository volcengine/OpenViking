import * as React from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { createFileRoute } from '@tanstack/react-router'
import {
  CircleAlertIcon,
  CircleDashedIcon,
  CircleHelpIcon,
  ExternalLinkIcon,
  KeyRoundIcon,
  ShieldCheckIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Alert, AlertDescription, AlertTitle } from '#/components/ui/alert'
import { Badge } from '#/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '#/components/ui/card'
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldLabel,
} from '#/components/ui/field'
import { Input } from '#/components/ui/input'
import { useAppConnection } from '#/hooks/use-app-connection'
import { probeStudioConnection } from '#/lib/admin'
import type { CapabilityProbeResult } from '#/lib/admin'
import { DEFAULT_ACCOUNT_ID, DEFAULT_USER_ID } from '#/lib/admin-options'
import { PLAIN_INPUT_PROPS } from '#/lib/form-input'
import { cn } from '#/lib/utils'
import type { ConnectionDraft } from '#/hooks/use-app-connection'

export const Route = createFileRoute('/settings')({
  component: ConnectionSettingsRoute,
})

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
        {result?.detail ? (
          <p className="mt-1 truncate text-xs text-muted-foreground">
            {result.detail}
          </p>
        ) : null}
      </div>
    </div>
  )
}

function UserApiKeyInput({
  accountId,
  id,
  onChange,
  placeholder,
  userId,
  value,
}: {
  accountId: string
  id: string
  onChange: (value: string) => void
  placeholder: string
  userId: string
  value: string
}) {
  const identity = `${accountId || DEFAULT_ACCOUNT_ID}/${userId || DEFAULT_USER_ID}`

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

function ConnectionSettingsRoute() {
  const { i18n, t } = useTranslation('settings')
  const { connection, saveConnection, serverMode } = useAppConnection()
  const [draft, setDraft] = React.useState<ConnectionDraft>(connection)
  const pendingDraftRef = React.useRef<ConnectionDraft | null>(null)
  const saveTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null)
  const saveConnectionRef = React.useRef(saveConnection)

  React.useEffect(() => {
    saveConnectionRef.current = saveConnection
  }, [saveConnection])

  React.useEffect(() => {
    if (!pendingDraftRef.current) {
      setDraft(connection)
    }
  }, [connection])

  React.useEffect(() => {
    return () => {
      if (saveTimerRef.current) {
        clearTimeout(saveTimerRef.current)
      }
      if (pendingDraftRef.current) {
        saveConnectionRef.current(pendingDraftRef.current)
      }
    }
  }, [])

  function updateDraft(next: Partial<ConnectionDraft>): void {
    const updated = { ...draft, ...next }
    setDraft(updated)
    pendingDraftRef.current = updated
    if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current)
    }
    saveTimerRef.current = setTimeout(() => {
      saveTimerRef.current = null
      const pending = pendingDraftRef.current
      pendingDraftRef.current = null
      if (pending) {
        saveConnectionRef.current(pending)
      }
    }, 350)
  }

  const probeQuery = useQuery({
    enabled: Boolean(connection.baseUrl) && serverMode !== 'checking',
    placeholderData: keepPreviousData,
    queryFn: () =>
      probeStudioConnection({
        accountId: connection.accountId || DEFAULT_ACCOUNT_ID,
        adminApiKey: connection.adminApiKey,
        apiKey: connection.apiKey,
        baseUrl: connection.baseUrl,
        serverMode,
        userId: connection.userId || DEFAULT_USER_ID,
      }),
    queryKey: [
      'studio-connection-probe',
      connection.baseUrl,
      connection.adminApiKey,
      connection.apiKey,
      connection.accountId,
      connection.userId,
      serverMode,
    ],
    retry: false,
    staleTime: 15_000,
  })
  const isDevMode = serverMode === 'dev'
  const rootApiKey = connection.adminApiKey.trim()
  const hasControlCredential = Boolean(draft.adminApiKey.trim())
  const hasDataCredential = Boolean(draft.apiKey.trim())
  const adminProbe = probeQuery.data?.admin
  const dataProbe = probeQuery.data?.data
  const hasAdminAccess = !isDevMode && adminProbe?.state === 'ok'
  const authenticationGuideUrl = i18n.resolvedLanguage?.startsWith('zh')
    ? 'https://docs.openviking.ai/zh/guides/04-authentication'
    : 'https://docs.openviking.ai/en/guides/04-authentication'
  const trustedCredentialRequired =
    serverMode === 'trusted' &&
    !probeQuery.isFetching &&
    !hasControlCredential &&
    (adminProbe?.state === 'error' || dataProbe?.state === 'error')
  const keyGuide =
    serverMode === 'trusted'
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
      <header className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight">
          {t('connectionPage.title')}
        </h1>
        <p className="max-w-3xl text-sm leading-6 text-muted-foreground">
          {t('connectionPage.description')}
        </p>
      </header>

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
          ) : (
            <>
              <div className="grid gap-4 md:grid-cols-2">
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
                      {t('connection.rootHint')}
                    </FieldDescription>
                  </FieldContent>
                </Field>
                <Field>
                  <FieldLabel htmlFor="settings-user-api-key">
                    {t('fields.userApiKey')}
                  </FieldLabel>
                  <FieldContent>
                    <UserApiKeyInput
                      accountId={draft.accountId}
                      id="settings-user-api-key"
                      userId={draft.userId}
                      value={draft.apiKey}
                      onChange={(apiKey) => updateDraft({ apiKey })}
                      placeholder={t('placeholders.userApiKey')}
                    />
                    <FieldDescription>
                      {t('connection.userHint')}
                    </FieldDescription>
                  </FieldContent>
                </Field>
              </div>

              <div className="grid gap-2 md:grid-cols-2">
                <CapabilityStatus
                  isLoading={probeQuery.isFetching}
                  label={t('health.admin')}
                  result={adminProbe}
                />
                <CapabilityStatus
                  isLoading={probeQuery.isFetching}
                  label={t('health.data')}
                  result={dataProbe}
                />
              </div>

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
        </CardContent>
      </Card>
    </div>
  )
}
