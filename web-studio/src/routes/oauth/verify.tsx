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
import { Field, FieldContent, FieldLabel } from '#/components/ui/field'
import {
  IdentityPicker,
  resolveEffectiveApiKey,
} from '#/components/identity-picker'
import type { IdentityPickerValue } from '#/components/identity-picker'
import { Input } from '#/components/ui/input'
import {
  summarizeConnectionIdentity,
  useAppConnection,
} from '#/hooks/use-app-connection'

type Phase =
  | { kind: 'idle' }
  | { kind: 'verifying' }
  | { kind: 'success'; clientName: string | null }
  | { kind: 'error'; message: string }

export const Route = createFileRoute('/oauth/verify')({
  component: VerifyPage,
})

function VerifyPage() {
  const { t } = useTranslation(['oauth', 'common'])
  const { connection, openConnectionDialog, serverMode } = useAppConnection()

  const currentIdentity = React.useMemo(() => {
    const summary = summarizeConnectionIdentity(connection, serverMode)
    return summary.values?.identity ?? t(summary.labelKey, { ns: 'connection' })
  }, [connection, serverMode, t])

  const [identityValue, setIdentityValue] = React.useState<IdentityPickerValue>(
    () =>
      connection.apiKey ? { mode: 'current' } : { mode: 'custom', apiKey: '' },
  )
  const [code, setCode] = React.useState('')
  const [phase, setPhase] = React.useState<Phase>({ kind: 'idle' })

  async function submit(
    event: React.FormEvent<HTMLFormElement>,
  ): Promise<void> {
    event.preventDefault()
    const effectiveKey = resolveEffectiveApiKey(
      identityValue,
      connection.apiKey,
    )
    if (!effectiveKey) {
      setPhase({ kind: 'error', message: t('verify.noApiKey') })
      return
    }
    const normalized = code.trim().toUpperCase()
    if (!normalized) {
      return
    }
    setPhase({ kind: 'verifying' })
    try {
      const resp = await fetch('/api/v1/auth/oauth-verify', {
        method: 'POST',
        cache: 'no-store',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${effectiveKey}`,
        },
        body: JSON.stringify({ code: normalized, decision: 'approve' }),
      })
      if (!resp.ok) {
        const text = await resp.text().catch(() => '')
        setPhase({
          kind: 'error',
          message: extractMessage(text) || String(resp.status),
        })
        return
      }
      const body = (await resp.json()) as {
        client_name?: string | null
      }
      setPhase({ kind: 'success', clientName: body.client_name ?? null })
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err)
      setPhase({ kind: 'error', message })
    }
  }

  const hasAnyKey = Boolean(
    resolveEffectiveApiKey(identityValue, connection.apiKey),
  )

  return (
    <div className="flex min-h-[60vh] w-full items-center justify-center px-4 py-8">
      <Card className="w-full max-w-lg">
        <CardHeader>
          <CardTitle>{t('verify.title')}</CardTitle>
          <CardDescription>{t('verify.description')}</CardDescription>
        </CardHeader>

        <form onSubmit={(event) => void submit(event)}>
          <CardContent className="grid gap-5">
            {phase.kind === 'success' ? (
              <p className="text-sm text-foreground">
                {phase.clientName
                  ? t('verify.success', { clientName: phase.clientName })
                  : t('verify.successUnknownClient')}
              </p>
            ) : (
              <>
                {!connection.apiKey ? (
                  <div className="grid gap-2 rounded-md border border-dashed p-3 text-sm">
                    <p>{t('verify.signInRequired')}</p>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="w-fit"
                      onClick={openConnectionDialog}
                      disabled={phase.kind === 'verifying'}
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
                  disabled={phase.kind === 'verifying'}
                />

                <Field>
                  <FieldLabel htmlFor="ov-oauth-verify-code">
                    {t('verify.codeLabel')}
                  </FieldLabel>
                  <FieldContent>
                    <Input
                      id="ov-oauth-verify-code"
                      autoFocus
                      autoComplete="off"
                      inputMode="text"
                      maxLength={12}
                      placeholder={t('verify.codePlaceholder')}
                      value={code}
                      onChange={(event) => setCode(event.target.value)}
                      disabled={phase.kind === 'verifying'}
                      className="font-mono uppercase tracking-widest"
                    />
                  </FieldContent>
                </Field>

                {phase.kind === 'error' ? (
                  <p className="text-sm text-destructive">
                    {t('verify.verifyError', { message: phase.message })}
                  </p>
                ) : null}
              </>
            )}
          </CardContent>

          {phase.kind === 'success' ? null : (
            <CardFooter className="justify-end">
              <Button
                type="submit"
                disabled={
                  phase.kind === 'verifying' ||
                  code.trim().length === 0 ||
                  !hasAnyKey
                }
              >
                {phase.kind === 'verifying'
                  ? t('consent.verifying')
                  : t('verify.submit')}
              </Button>
            </CardFooter>
          )}
        </form>
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
