import * as React from 'react'
import { useTranslation } from 'react-i18next'
import { createFileRoute } from '@tanstack/react-router'

import { Button } from '#/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '#/components/ui/card'
import {
  IdentityPicker,
  resolveEffectiveApiKey,
} from '#/components/identity-picker'
import type { IdentityPickerValue } from '#/components/identity-picker'
import {
  summarizeConnectionIdentity,
  useAppConnection,
} from '#/hooks/use-app-connection'

type ConsentSearch = {
  pending: string
}

type PendingInfo = {
  client_id: string
  client_name: string | null
  redirect_host: string | null
  scopes: string[]
}

type Phase =
  | { kind: 'loading' }
  | { kind: 'ready' }
  | { kind: 'verifying' }
  | { kind: 'denying' }
  | { kind: 'denied' }
  | { kind: 'waitingRedirect' }
  | { kind: 'expired' }
  | { kind: 'error'; message: string }

export const Route = createFileRoute('/oauth/consent')({
  validateSearch: (search: Record<string, unknown>): ConsentSearch => ({
    pending: typeof search.pending === 'string' ? search.pending : '',
  }),
  component: ConsentPage,
})

function ConsentPage() {
  const { t } = useTranslation(['oauth', 'common'])
  const { pending } = Route.useSearch()
  const { connection, openConnectionDialog, serverMode } = useAppConnection()

  const currentIdentity = React.useMemo(() => {
    const summary = summarizeConnectionIdentity(connection, serverMode)
    return summary.values?.identity ?? t(summary.labelKey, { ns: 'connection' })
  }, [connection, serverMode, t])

  const [pendingInfo, setPendingInfo] = React.useState<PendingInfo | null>(null)
  const [phase, setPhase] = React.useState<Phase>({ kind: 'loading' })
  const [identityValue, setIdentityValue] = React.useState<IdentityPickerValue>(
    () =>
      connection.apiKey ? { mode: 'current' } : { mode: 'custom', apiKey: '' },
  )

  React.useEffect(() => {
    if (!pending) {
      setPhase({ kind: 'error', message: t('consent.missingPending') })
      return
    }

    let cancelled = false
    setPhase({ kind: 'loading' })

    void fetch(`/api/v1/auth/oauth/pending/${encodeURIComponent(pending)}`, {
      cache: 'no-store',
    })
      .then(async (resp) => {
        if (cancelled) return
        if (resp.status === 404 || resp.status === 410) {
          setPhase({ kind: 'expired' })
          return
        }
        if (!resp.ok) {
          const text = await resp.text().catch(() => '')
          setPhase({
            kind: 'error',
            message: text.slice(0, 200) || String(resp.status),
          })
          return
        }
        const body = (await resp.json()) as PendingInfo
        setPendingInfo(body)
        setPhase({ kind: 'ready' })
      })
      .catch((err: unknown) => {
        if (cancelled) return
        const message = err instanceof Error ? err.message : String(err)
        setPhase({ kind: 'error', message })
      })

    return () => {
      cancelled = true
    }
  }, [pending, t])

  async function pollStatusAndRedirect(): Promise<void> {
    for (;;) {
      try {
        const resp = await fetch(
          `/oauth/authorize/page/status?pending=${encodeURIComponent(pending)}`,
          { cache: 'no-store' },
        )
        if (resp.status === 410) {
          setPhase({ kind: 'expired' })
          return
        }
        const body = (await resp.json()) as {
          status: string
          redirect_url?: string
        }
        if (body.status === 'approved' && body.redirect_url) {
          window.location.replace(body.redirect_url)
          return
        }
      } catch {
        // Transient — retry.
      }
      await new Promise((r) => setTimeout(r, 1000))
    }
  }

  async function postVerify(decision: 'approve' | 'deny'): Promise<void> {
    const effectiveKey = resolveEffectiveApiKey(
      identityValue,
      connection.apiKey,
    )
    if (decision === 'approve' && !effectiveKey) {
      setPhase({ kind: 'error', message: t('consent.noApiKey') })
      return
    }
    setPhase({ kind: decision === 'approve' ? 'verifying' : 'denying' })
    try {
      const resp = await fetch('/api/v1/auth/oauth-verify', {
        method: 'POST',
        cache: 'no-store',
        headers: {
          'Content-Type': 'application/json',
          ...(effectiveKey ? { Authorization: `Bearer ${effectiveKey}` } : {}),
        },
        body: JSON.stringify({ pending_id: pending, decision }),
      })
      if (!resp.ok) {
        const text = await resp.text().catch(() => '')
        setPhase({
          kind: 'error',
          message: extractMessage(text) || String(resp.status),
        })
        return
      }
      if (decision === 'deny') {
        setPhase({ kind: 'denied' })
        return
      }
      setPhase({ kind: 'waitingRedirect' })
      void pollStatusAndRedirect()
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err)
      setPhase({ kind: 'error', message })
    }
  }

  const clientName =
    pendingInfo?.client_name || pendingInfo?.client_id || 'MCP client'
  const fallbackHref = `/oauth/authorize/page?pending=${encodeURIComponent(pending)}`
  const hasAnyKey = Boolean(
    resolveEffectiveApiKey(identityValue, connection.apiKey),
  )

  return (
    <div className="flex min-h-[60vh] w-full items-center justify-center px-4 py-8">
      <Card className="w-full max-w-lg">
        <CardHeader>
          <CardTitle>{t('consent.title', { clientName })}</CardTitle>
          {phase.kind === 'ready' && pendingInfo ? (
            <CardDescription>
              {t('consent.requestSummary', { clientName })}
            </CardDescription>
          ) : null}
        </CardHeader>

        <CardContent className="grid gap-5">
          {phase.kind === 'loading' ? (
            <p className="text-sm text-muted-foreground">
              {t('consent.loading')}
            </p>
          ) : null}

          {phase.kind === 'expired' ? (
            <p className="text-sm text-destructive">{t('consent.expired')}</p>
          ) : null}

          {phase.kind === 'error' ? (
            <p className="text-sm text-destructive">
              {t('consent.verifyError', { message: phase.message })}
            </p>
          ) : null}

          {pendingInfo &&
          (phase.kind === 'ready' ||
            phase.kind === 'verifying' ||
            phase.kind === 'denying' ||
            phase.kind === 'waitingRedirect') ? (
            <>
              <dl className="grid gap-2 text-sm">
                <div className="flex justify-between gap-4">
                  <dt className="text-muted-foreground">
                    {t('consent.redirectLabel')}
                  </dt>
                  <dd className="font-mono text-foreground break-all">
                    {pendingInfo.redirect_host ?? '—'}
                  </dd>
                </div>
                <div className="flex justify-between gap-4">
                  <dt className="text-muted-foreground">
                    {t('consent.scopesLabel')}
                  </dt>
                  <dd className="font-mono text-foreground break-all">
                    {pendingInfo.scopes.length > 0
                      ? pendingInfo.scopes.join(' ')
                      : t('consent.scopesNone')}
                  </dd>
                </div>
              </dl>

              {!connection.apiKey ? (
                <div className="grid gap-2 rounded-md border border-dashed p-3 text-sm">
                  <p>{t('consent.signInRequired')}</p>
                  <Button
                    variant="outline"
                    size="sm"
                    className="w-fit"
                    onClick={openConnectionDialog}
                    disabled={
                      phase.kind === 'verifying' || phase.kind === 'denying'
                    }
                  >
                    {t('consent.openConnectionDialog')}
                  </Button>
                </div>
              ) : null}

              <IdentityPicker
                value={identityValue}
                onChange={setIdentityValue}
                currentApiKey={connection.apiKey}
                currentIdentityLabel={currentIdentity}
                disabled={
                  phase.kind === 'verifying' ||
                  phase.kind === 'denying' ||
                  phase.kind === 'waitingRedirect'
                }
              />

              {phase.kind === 'waitingRedirect' ? (
                <p className="text-sm text-muted-foreground">
                  {t('consent.waitingRedirect')}
                </p>
              ) : null}
            </>
          ) : null}

          {phase.kind === 'denied' ? (
            <p className="text-sm text-muted-foreground">
              {t('consent.denied')}
            </p>
          ) : null}
        </CardContent>

        {pendingInfo &&
        (phase.kind === 'ready' ||
          phase.kind === 'verifying' ||
          phase.kind === 'denying') ? (
          <CardFooter className="flex flex-col gap-3">
            <div className="flex w-full justify-end gap-2">
              <Button
                variant="outline"
                onClick={() => void postVerify('deny')}
                disabled={
                  phase.kind === 'verifying' || phase.kind === 'denying'
                }
              >
                {phase.kind === 'denying'
                  ? t('consent.denying')
                  : t('consent.deny')}
              </Button>
              <Button
                onClick={() => void postVerify('approve')}
                disabled={
                  phase.kind === 'verifying' ||
                  phase.kind === 'denying' ||
                  !hasAnyKey
                }
              >
                {phase.kind === 'verifying'
                  ? t('consent.verifying')
                  : t('consent.authorize')}
              </Button>
            </div>
            <a
              href={fallbackHref}
              className="self-end text-xs text-muted-foreground hover:text-foreground"
            >
              {t('consent.useAnotherDevice')}
            </a>
          </CardFooter>
        ) : null}
      </Card>
    </div>
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
