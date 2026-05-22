import * as React from 'react'
import { useTranslation } from 'react-i18next'
import { ExternalLinkIcon } from 'lucide-react'

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
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '#/components/ui/dialog'
import {
  IdentityPicker,
  resolveEffectiveApiKey,
} from '#/components/identity-picker'
import type { IdentityPickerValue } from '#/components/identity-picker'
import {
  summarizeConnectionIdentity,
  useAppConnection,
} from '#/hooks/use-app-connection'

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

const OAUTH_DOCS_URL =
  'https://github.com/volcengine/OpenViking/blob/main/docs/en/guides/11-oauth.md'

export function OAuthSetupDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { t } = useTranslation(['oauthSetup', 'connection', 'oauth', 'common'])
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t('page.title', { ns: 'oauthSetup' })}</DialogTitle>
          <DialogDescription>
            {t('page.intro', { ns: 'oauthSetup' })}
          </DialogDescription>
        </DialogHeader>
        <a
          href={OAUTH_DOCS_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline"
        >
          {t('page.docsLink', { ns: 'oauthSetup' })}
          <ExternalLinkIcon className="size-3.5" />
        </a>
        <OAuthOtpForm />
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('cancel', { ns: 'common', keyPrefix: 'action' })}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function OAuthOtpForm() {
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
    <Card className="gap-3">
      <CardHeader className="gap-1.5">
        <CardTitle className="text-base">
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

        <div className="flex justify-end">
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
