import * as React from 'react'
import { useTranslation } from 'react-i18next'

import {
  summarizeConnectionIdentity,
  useAppConnection,
} from '#/hooks/use-app-connection'

import { Button } from '#/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '#/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '#/components/ui/dialog'
import {
  Field,
  FieldContent,
  FieldGroup,
  FieldLabel,
  FieldSet,
} from '#/components/ui/field'
import {
  IdentityPicker,
  resolveEffectiveApiKey,
} from '#/components/identity-picker'
import type { IdentityPickerValue } from '#/components/identity-picker'
import { Input } from '#/components/ui/input'

type OtpResult = {
  otp: string
  expires_at: number
  ttl_seconds: number
}

type OtpPhase =
  | { kind: 'idle' }
  | { kind: 'requesting' }
  | { kind: 'ready'; result: OtpResult }
  | { kind: 'error'; message: string }

export function ConnectionDialog() {
  const { t } = useTranslation(['connection', 'oauth', 'common'])
  const {
    connection,
    isConnectionDialogOpen,
    saveConnection,
    serverMode,
    setConnectionDialogOpen,
  } = useAppConnection()
  const [draft, setDraft] = React.useState(connection)
  const [showAdvancedInDevMode, setShowAdvancedInDevMode] =
    React.useState(false)

  React.useEffect(() => {
    if (isConnectionDialogOpen) {
      setDraft(connection)
      setShowAdvancedInDevMode(false)
    }
  }, [connection, isConnectionDialogOpen])

  const isDevImplicit = serverMode === 'dev-implicit'
  const showIdentityFields = !isDevImplicit || showAdvancedInDevMode

  return (
    <Dialog
      open={isConnectionDialogOpen}
      onOpenChange={setConnectionDialogOpen}
    >
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t('dialog.title', { ns: 'connection' })}</DialogTitle>
        </DialogHeader>

        <FieldSet>
          <FieldGroup>
            <Field>
              <FieldLabel htmlFor="ov-base-url">
                {t('fields.baseUrl.label', { ns: 'connection' })}
              </FieldLabel>
              <FieldContent>
                <Input
                  id="ov-base-url"
                  placeholder={t('fields.baseUrl.placeholder', {
                    ns: 'connection',
                  })}
                  value={draft.baseUrl}
                  onChange={(event) =>
                    setDraft((current) => ({
                      ...current,
                      baseUrl: event.target.value,
                    }))
                  }
                />
              </FieldContent>
            </Field>

            <Card
              size="sm"
              className="min-h-56 gap-3 border bg-background/70 shadow-none"
            >
              {showIdentityFields ? (
                <>
                  <CardHeader className="gap-1.5">
                    <CardTitle className="text-sm">
                      {t('fields.credentials.title', { ns: 'connection' })}
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="grid flex-1 content-start gap-3">
                    <div className="grid gap-3 md:grid-cols-2">
                      <Field>
                        <FieldLabel htmlFor="ov-account-id">
                          {t('fields.accountId.label', { ns: 'connection' })}
                        </FieldLabel>
                        <FieldContent>
                          <Input
                            id="ov-account-id"
                            placeholder={t('fields.accountId.placeholder', {
                              ns: 'connection',
                            })}
                            value={draft.accountId}
                            onChange={(event) =>
                              setDraft((current) => ({
                                ...current,
                                accountId: event.target.value,
                              }))
                            }
                          />
                        </FieldContent>
                      </Field>
                      <Field>
                        <FieldLabel htmlFor="ov-user-id">
                          {t('fields.userId.label', { ns: 'connection' })}
                        </FieldLabel>
                        <FieldContent>
                          <Input
                            id="ov-user-id"
                            placeholder={t('fields.userId.placeholder', {
                              ns: 'connection',
                            })}
                            value={draft.userId}
                            onChange={(event) =>
                              setDraft((current) => ({
                                ...current,
                                userId: event.target.value,
                              }))
                            }
                          />
                        </FieldContent>
                      </Field>
                    </div>

                    <Field>
                      <FieldLabel htmlFor="ov-api-key">
                        {t('fields.apiKey.label', { ns: 'connection' })}
                      </FieldLabel>
                      <FieldContent>
                        <Input
                          id="ov-api-key"
                          type="password"
                          placeholder={t('fields.apiKey.placeholder', {
                            ns: 'connection',
                          })}
                          value={draft.apiKey}
                          onChange={(event) =>
                            setDraft((current) => ({
                              ...current,
                              apiKey: event.target.value,
                            }))
                          }
                        />
                      </FieldContent>
                    </Field>
                  </CardContent>
                </>
              ) : (
                <>
                  <CardHeader className="gap-2">
                    <CardTitle className="text-sm">
                      {t('devMode.title', { ns: 'connection' })}
                    </CardTitle>
                    <CardDescription>
                      {t('devMode.description', { ns: 'connection' })}
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="flex flex-1 items-end">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setShowAdvancedInDevMode(true)}
                    >
                      {t('showAdvancedIdentityFields', {
                        ns: 'common',
                        keyPrefix: 'action',
                      })}
                    </Button>
                  </CardContent>
                </>
              )}
            </Card>

            <OAuthOtpSection />
          </FieldGroup>
        </FieldSet>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => setConnectionDialogOpen(false)}
          >
            {t('cancel', { ns: 'common', keyPrefix: 'action' })}
          </Button>
          <Button
            onClick={() => {
              saveConnection(draft)
              setConnectionDialogOpen(false)
            }}
          >
            {t('saveConnection', { ns: 'common', keyPrefix: 'action' })}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function OAuthOtpSection() {
  const { t } = useTranslation(['connection', 'oauth'])
  const { connection, serverMode } = useAppConnection()
  const currentIdentity = React.useMemo(() => {
    const summary = summarizeConnectionIdentity(connection, serverMode)
    return summary.values?.identity ?? t(summary.labelKey, { ns: 'connection' })
  }, [connection, serverMode, t])

  const [identityValue, setIdentityValue] = React.useState<IdentityPickerValue>(
    () =>
      connection.apiKey ? { mode: 'current' } : { mode: 'custom', apiKey: '' },
  )
  const [phase, setPhase] = React.useState<OtpPhase>({ kind: 'idle' })
  const [remaining, setRemaining] = React.useState(0)
  const [copied, setCopied] = React.useState(false)

  React.useEffect(() => {
    if (phase.kind !== 'ready') {
      return
    }
    const tick = () => {
      const now = Math.floor(Date.now() / 1000)
      setRemaining(Math.max(0, phase.result.expires_at - now))
    }
    tick()
    const id = window.setInterval(tick, 1000)
    return () => window.clearInterval(id)
  }, [phase])

  React.useEffect(() => {
    if (!copied) return
    const id = window.setTimeout(() => setCopied(false), 1500)
    return () => window.clearTimeout(id)
  }, [copied])

  async function generateOtp(): Promise<void> {
    const effectiveKey = resolveEffectiveApiKey(
      identityValue,
      connection.apiKey,
    )
    if (!effectiveKey) {
      setPhase({
        kind: 'error',
        message: t('identityPicker.noCurrent', { ns: 'oauth' }),
      })
      return
    }
    setPhase({ kind: 'requesting' })
    try {
      const resp = await fetch('/api/v1/auth/otp', {
        method: 'POST',
        cache: 'no-store',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${effectiveKey}`,
        },
        body: '{}',
      })
      if (!resp.ok) {
        const text = await resp.text().catch(() => '')
        setPhase({
          kind: 'error',
          message: extractMessage(text) || String(resp.status),
        })
        return
      }
      const result = (await resp.json()) as OtpResult
      setPhase({ kind: 'ready', result })
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err)
      setPhase({ kind: 'error', message })
    }
  }

  async function copyOtp(): Promise<void> {
    if (phase.kind !== 'ready') return
    try {
      await navigator.clipboard.writeText(phase.result.otp)
      setCopied(true)
    } catch {
      // Clipboard may be unavailable (insecure context); ignore.
    }
  }

  const hasAnyKey = Boolean(
    resolveEffectiveApiKey(identityValue, connection.apiKey),
  )
  const expired = phase.kind === 'ready' && remaining <= 0

  return (
    <Card size="sm" className="gap-3 border bg-background/70 shadow-none">
      <CardHeader className="gap-1.5">
        <CardTitle className="text-sm">
          {t('oauthOtp.title', { ns: 'connection' })}
        </CardTitle>
        <CardDescription>
          {t('oauthOtp.description', { ns: 'connection' })}
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <IdentityPicker
          value={identityValue}
          onChange={setIdentityValue}
          currentApiKey={connection.apiKey}
          currentIdentityLabel={currentIdentity}
          disabled={phase.kind === 'requesting'}
        />

        {phase.kind === 'ready' && !expired ? (
          <div className="grid gap-2 rounded-md border bg-muted/40 p-3">
            <span className="text-xs text-muted-foreground">
              {t('oauthOtp.codeLabel', { ns: 'connection' })}
            </span>
            <div className="flex items-center gap-3">
              <span className="font-mono text-2xl tracking-[0.4em]">
                {phase.result.otp}
              </span>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => void copyOtp()}
              >
                {copied
                  ? t('oauthOtp.copied', { ns: 'connection' })
                  : t('oauthOtp.copy', { ns: 'connection' })}
              </Button>
            </div>
            <span className="text-xs text-muted-foreground">
              {t('oauthOtp.expiresIn', {
                ns: 'connection',
                seconds: remaining,
              })}
            </span>
          </div>
        ) : null}

        {expired ? (
          <p className="text-xs text-destructive">
            {t('oauthOtp.expired', { ns: 'connection' })}
          </p>
        ) : null}

        {phase.kind === 'error' ? (
          <p className="text-xs text-destructive">
            {t('oauthOtp.generateError', {
              ns: 'connection',
              message: phase.message,
            })}
          </p>
        ) : null}

        <div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void generateOtp()}
            disabled={phase.kind === 'requesting' || !hasAnyKey}
          >
            {phase.kind === 'ready' && !expired
              ? t('oauthOtp.regenerate', { ns: 'connection' })
              : t('oauthOtp.generate', { ns: 'connection' })}
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

function extractMessage(raw: string): string {
  try {
    const parsed = JSON.parse(raw) as { error?: { message?: string } }
    return parsed.error?.message || raw.slice(0, 200)
  } catch {
    return raw.slice(0, 200)
  }
}
