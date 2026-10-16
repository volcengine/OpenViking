import * as React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CopyIcon, LoaderCircleIcon, PowerIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { Button } from '#/components/ui/button'
import { Checkbox } from '#/components/ui/checkbox'
import { Input } from '#/components/ui/input'
import { useAppConnection } from '#/hooks/use-app-connection'
import { copyTextToClipboard } from '#/lib/clipboard'
import {
  createConfigFileApi,
  waitForServerRestart,
} from '#/routes/settings/-lib/config-file-api'
import type { ConfigFileConfiguration } from '#/routes/settings/-lib/config-file-api'

import {
  enableGatewayContent,
  generateManagementKey,
  isRevisionConflict,
} from '../-lib/enable-gateway'
import { setManagementKey } from '../-lib/management-key'
import { CodeBlock } from './code-block'
import { ConfirmDialog } from './confirm-dialog'
import { Notice } from './notice'
import { SetupCard } from './setup-card'

/** The file was saved, but the restart could not be confirmed. */
class RestartError extends Error {}

type Outcome = 'saved' | 'restarted' | 'rolledBack'

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

/**
 * Shows a new gateway management key once, with a copy button. With
 * `openGateway` the key unlocks this tab once the gateway opens; otherwise
 * root enters it after restarting the server.
 */
function ManagementKeyReveal({
  value,
  openGateway,
  onDismiss,
}: {
  value: string
  openGateway?: () => void
  onDismiss: () => void
}) {
  const { t } = useTranslation('gateway')
  const input = React.useRef<HTMLInputElement>(null)
  const [copyFailed, setCopyFailed] = React.useState(false)

  async function copy() {
    try {
      await copyTextToClipboard(value)
      setCopyFailed(false)
      toast.success(t('copy.done'))
    } catch {
      // Leave the key selected so it can be copied by hand.
      setCopyFailed(true)
      input.current?.focus()
      input.current?.select()
    }
  }

  return (
    <Notice
      tone="warning"
      title={t('unavailable.enable.keyTitle')}
      action={
        <Button size="sm" onClick={openGateway ?? onDismiss}>
          {t(
            openGateway
              ? 'unavailable.enable.continue'
              : 'unavailable.enable.keySaved',
          )}
        </Button>
      }
    >
      <p>
        {t(
          openGateway
            ? 'unavailable.enable.keyOnce'
            : 'unavailable.enable.keyOnceManual',
        )}
      </p>
      <div className="flex min-w-0 items-center gap-2">
        <Input
          ref={input}
          readOnly
          value={value}
          aria-label={t('unavailable.enable.keyTitle')}
          className="bg-background font-mono text-xs text-foreground"
          onFocus={(event) => event.currentTarget.select()}
        />
        <Button
          variant="outline"
          size="sm"
          aria-label={t('unavailable.enable.copyKey')}
          onClick={() => void copy()}
        >
          <CopyIcon />
          {t('actions.copy')}
        </Button>
      </div>
      {copyFailed ? (
        <p role="alert">{t('unavailable.enable.copyFailed')}</p>
      ) : null}
    </Notice>
  )
}

/**
 * The "not enabled" card for root on an OpenViking server: turns the gateway
 * on by writing `gateway.enabled` to ov.conf and restarting the server, with
 * the same configuration API the settings page uses.
 */
export function EnableGatewayCard({
  retry,
  retrying,
  onOpenGateway,
  fallback,
}: {
  /** Retry button that asks the gateway for /access again. */
  retry: React.ReactNode
  retrying: boolean
  /**
   * Asks for /access again once the server runs with the gateway on;
   * resolves true when the gateway answers.
   */
  onOpenGateway: () => Promise<boolean>
  /** Shown when ov.conf can't be read through the configuration API. */
  fallback: React.ReactNode
}) {
  const { t } = useTranslation('gateway')
  const { connection, serverMode, identityScopeKey } = useAppConnection()
  const queryClient = useQueryClient()
  const api = React.useMemo(
    () => createConfigFileApi(connection, serverMode === 'trusted'),
    [connection, serverMode],
  )
  // Shared with the settings page, which reads the same file.
  const queryKey = React.useMemo(
    () => ['server-configuration', identityScopeKey],
    [identityScopeKey],
  )
  const config = useQuery({
    queryKey,
    queryFn: () => api.get(),
    retry: false,
  })
  const [confirming, setConfirming] = React.useState(false)
  const [protect, setProtect] = React.useState(false)
  const [issuedKey, setIssuedKey] = React.useState<string | null>(null)
  const [restarted, setRestarted] = React.useState(false)
  const restartAbort = React.useRef<AbortController | null>(null)
  React.useEffect(() => () => restartAbort.current?.abort(), [])

  const enable = useMutation({
    mutationFn: async ({
      file,
      key,
    }: {
      file: ConfigFileConfiguration
      key?: string
    }): Promise<Outcome> => {
      const saved = await api.save(
        enableGatewayContent(file.content, key),
        file.revision,
      )
      if (!file.restart?.supported) return 'saved'
      const controller = new AbortController()
      restartAbort.current = controller
      try {
        const accepted = await api.restart(saved.revision)
        const result = await waitForServerRestart(
          () => api.get(3000),
          accepted.instance_id,
          controller.signal,
        )
        queryClient.setQueryData(queryKey, result)
        return result.restart?.rolled_back ? 'rolledBack' : 'restarted'
      } catch (error) {
        if (isRevisionConflict(error)) throw error
        throw new RestartError(errorMessage(error), { cause: error })
      }
    },
    onSuccess: (outcome, { key }) => {
      if (outcome === 'saved') void queryClient.invalidateQueries({ queryKey })
      if (outcome === 'rolledBack') return
      // The key is stored only when the reveal is dismissed: storing it
      // changes the gateway query scope, which replaces this card.
      if (key) setIssuedKey(key)
      else if (outcome === 'restarted') void openGateway()
    },
    onError: (error, { key }) => {
      // ov.conf already holds the key; this is the only chance to show it.
      if (key && error instanceof RestartError) setIssuedKey(key)
    },
  })

  async function openGateway(key?: string) {
    const baseUrl = connection.baseUrl
    if (await onOpenGateway()) {
      // Unlock this tab: root set the key and can edit right away. The card
      // is usually gone by now, so this touches no component state.
      if (key) setManagementKey(baseUrl, key)
    } else setRestarted(true)
  }

  if (config.isPending)
    return (
      <SetupCard
        title={t('unavailable.notEnabled.title')}
        description={t('unavailable.enable.checking')}
        actions={retry}
      />
    )
  if (config.isError) return fallback
  const file = config.data

  if (file.writable === false)
    return (
      <SetupCard
        title={t('unavailable.notEnabled.title')}
        description={t('unavailable.enable.readOnly')}
        actions={retry}
      >
        <CodeBlock
          label={t('unavailable.environment')}
          code="OPENVIKING_GATEWAY_ENABLED=true"
        />
        <CodeBlock
          label={t('unavailable.enable.helm')}
          code={'gateway:\n  enabled: true'}
        />
      </SetupCard>
    )

  const canRestart = Boolean(file.restart?.supported)
  const outcome = enable.data
  const done = enable.isSuccess && outcome !== 'rolledBack'

  return (
    <SetupCard
      title={t('unavailable.notEnabled.title')}
      description={t(
        canRestart
          ? 'unavailable.enable.description'
          : 'unavailable.enable.descriptionManualRestart',
      )}
      actions={
        // Asking for /access mid-restart would replace this card with an error.
        enable.isPending ? null : (
          <>
            {done ? null : (
              <Button size="sm" onClick={() => setConfirming(true)}>
                <PowerIcon />
                {t('unavailable.enable.action')}
              </Button>
            )}
            {retry}
          </>
        )
      }
    >
      {enable.isPending ? (
        <p
          role="status"
          className="flex items-center gap-2 text-sm text-muted-foreground"
        >
          <LoaderCircleIcon className="size-4 animate-spin" />
          {t(
            canRestart
              ? 'unavailable.enable.restarting'
              : 'unavailable.enable.saving',
          )}
        </p>
      ) : null}
      {enable.isError ? (
        isRevisionConflict(enable.error) ? (
          <Notice
            tone="danger"
            action={
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  enable.reset()
                  void config.refetch()
                }}
              >
                {t('unavailable.enable.reload')}
              </Button>
            }
          >
            {t('unavailable.enable.conflict')}
          </Notice>
        ) : (
          <Notice tone="danger">
            {t(
              enable.error instanceof RestartError
                ? 'unavailable.enable.restartFailed'
                : 'unavailable.enable.failed',
              { message: errorMessage(enable.error) },
            )}
          </Notice>
        )
      ) : null}
      {outcome === 'rolledBack' ? (
        <Notice tone="danger">{t('unavailable.enable.rolledBack')}</Notice>
      ) : null}
      {outcome === 'saved' ? (
        <Notice tone="info">{t('unavailable.enable.savedRestart')}</Notice>
      ) : null}
      {issuedKey ? (
        <ManagementKeyReveal
          value={issuedKey}
          openGateway={
            outcome === 'restarted'
              ? () => void openGateway(issuedKey)
              : undefined
          }
          onDismiss={() => setIssuedKey(null)}
        />
      ) : null}
      {restarted && !retrying ? (
        <Notice tone="warning">{t('unavailable.enable.stillOff')}</Notice>
      ) : null}
      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        destructive={false}
        title={t('unavailable.enable.confirmTitle')}
        description={t(
          canRestart
            ? 'unavailable.enable.confirmRestart'
            : 'unavailable.enable.confirmManualRestart',
        )}
        confirmLabel={t('unavailable.enable.action')}
        icon={<PowerIcon />}
        onConfirm={() =>
          enable.mutate({
            file,
            key: protect ? generateManagementKey() : undefined,
          })
        }
      >
        <label className="flex items-start gap-2.5 text-sm leading-6">
          <Checkbox
            className="mt-1"
            checked={protect}
            onCheckedChange={(checked) => setProtect(checked === true)}
          />
          <span className="grid gap-0.5">
            <span>{t('unavailable.enable.protect')}</span>
            <span className="text-muted-foreground">
              {t('unavailable.enable.protectHint')}
            </span>
          </span>
        </label>
      </ConfirmDialog>
    </SetupCard>
  )
}
