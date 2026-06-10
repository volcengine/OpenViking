import * as React from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '#/components/ui/button'
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
import { useIdentityDirectory } from '#/hooks/use-identity-directory'

type Phase =
  | { kind: 'idle' }
  | { kind: 'verifying' }
  | { kind: 'success'; clientName: string | null }
  | { kind: 'error'; message: string }

/**
 * Cross-device verify form: enter the 6-character `display_code` shown on the
 * device that started the MCP client login, pick an identity, and approve.
 *
 * Rendered both as the standalone `/oauth/verify` route and inside the sidebar
 * footer's verify dialog, so it carries its own chrome-free layout and the host
 * (Card or Dialog) supplies the title/footer.
 */
export function CrossDeviceVerifyForm() {
  const { t } = useTranslation(['oauth', 'common'])
  const {
    connection,
    isConnectionRoleLoading,
    openConnectionSettings,
    serverMode,
  } = useAppConnection()
  const directory = useIdentityDirectory()

  const currentIdentity = React.useMemo(() => {
    const summary = summarizeConnectionIdentity(connection, serverMode)
    return summary.values?.identity ?? t(summary.labelKey, { ns: 'connection' })
  }, [connection, serverMode, t])

  const [identityValue, setIdentityValue] = React.useState<IdentityPickerValue>(
    () =>
      connection.apiKey ? { mode: 'current' } : { mode: 'custom', apiKey: '' },
  )
  // Set once the user manually picks a mode, so the default-mode effect stops
  // overriding their choice.
  const userTouchedRef = React.useRef(false)
  const handleIdentityChange = React.useCallback(
    (next: IdentityPickerValue) => {
      userTouchedRef.current = true
      setIdentityValue(next)
    },
    [],
  )

  // Default-mode selection (see consent.tsx for the rationale): root has no
  // usable "current" identity, so default to selecting a concrete account/user
  // once the directory is available.
  React.useEffect(() => {
    if (userTouchedRef.current || isConnectionRoleLoading) {
      return
    }
    const desiredMode = connection.apiKey
      ? 'current'
      : directory.available
        ? 'select'
        : 'custom'
    setIdentityValue((prev) => {
      if (prev.mode === desiredMode) {
        return prev
      }
      if (desiredMode === 'current') {
        return { mode: 'current' }
      }
      if (desiredMode === 'select') {
        return {
          mode: 'select',
          accountId: directory.selectedAccountId,
          userId: directory.selectedUserId,
          apiKey:
            directory.resolveUserKey(
              directory.selectedAccountId,
              directory.selectedUserId,
            ) ?? '',
        }
      }
      return { mode: 'custom', apiKey: '' }
    })
  }, [connection.apiKey, directory, isConnectionRoleLoading])

  // Keep the select payload in sync with the directory while in select mode.
  React.useEffect(() => {
    setIdentityValue((prev) => {
      if (prev.mode !== 'select') {
        return prev
      }
      const accountId = directory.selectedAccountId
      const userId = directory.selectedUserId
      const apiKey = directory.resolveUserKey(accountId, userId) ?? ''
      if (
        prev.accountId === accountId &&
        prev.userId === userId &&
        prev.apiKey === apiKey
      ) {
        return prev
      }
      return { mode: 'select', accountId, userId, apiKey }
    })
  }, [directory])

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

  if (phase.kind === 'success') {
    return (
      <p className="text-sm text-foreground">
        {phase.clientName
          ? t('verify.success', { clientName: phase.clientName })
          : t('verify.successUnknownClient')}
      </p>
    )
  }

  return (
    <form onSubmit={(event) => void submit(event)} className="grid gap-5">
      {!connection.apiKey && !directory.available ? (
        <div className="grid gap-2 rounded-md border border-dashed p-3 text-sm">
          <p>{t('verify.signInRequired')}</p>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="w-fit"
            onClick={openConnectionSettings}
            disabled={phase.kind === 'verifying'}
          >
            {t('consent.openConnectionSettings')}
          </Button>
        </div>
      ) : null}

      <IdentityPicker
        value={identityValue}
        onChange={handleIdentityChange}
        currentApiKey={connection.apiKey}
        currentIdentityLabel={currentIdentity}
        directory={directory}
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

      <div className="flex justify-end">
        <Button
          type="submit"
          disabled={
            phase.kind === 'verifying' || code.trim().length === 0 || !hasAnyKey
          }
        >
          {phase.kind === 'verifying'
            ? t('consent.verifying')
            : t('verify.submit')}
        </Button>
      </div>
    </form>
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
