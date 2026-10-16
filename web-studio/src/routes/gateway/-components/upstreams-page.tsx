import * as React from 'react'
import {
  useMutation,
  useMutationState,
  useQueryClient,
} from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import {
  EllipsisIcon,
  PencilIcon,
  PlusIcon,
  RefreshCwIcon,
  ServerIcon,
  Trash2Icon,
  TriangleAlertIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Badge } from '#/components/ui/badge'
import { Button } from '#/components/ui/button'
import { Card, CardContent } from '#/components/ui/card'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '#/components/ui/dropdown-menu'
import { Switch } from '#/components/ui/switch'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '#/components/ui/table'
import { cn } from '#/lib/utils'

import { importVlm, keysUsing, saveUpstream, toGatewayError } from '../-lib/api'
import type { Upstream, VlmImportResult } from '../-lib/api'
import { formatNumber, hostFromUrl } from '../-lib/format'
import { toastGatewayError } from '../-lib/error-toast'
import {
  authModeLabel,
  gatewayErrorMessage,
  vendorLabel,
} from '../-lib/localize'
import { NEW_ID } from '../-lib/search'
import { toUpstreamInput } from '../-lib/upstream-schema'
import {
  gatewayQueryKey,
  useGateway,
  useKeys,
  useUpstreams,
} from '../-lib/use-gateway'
import { EmptyState, ErrorState, LoadingState } from './empty-state'
import { Notice } from './notice'
import { SectionHeader } from './section-header'
import { EmptyValue, ProtocolBadge, ToneBadge } from './status-badges'
import { DeleteUpstreamDialog, UpstreamTest } from './upstreams-actions'

/** Models shown as chips before the rest collapse into "+N". */
const VISIBLE_MODELS = 3

/**
 * Columns left out below `md`, so a row keeps its name, switch and actions on
 * screen; the name cell then shows the protocol under the host instead.
 */
const DESKTOP_ONLY = 'hidden md:table-cell'

type ToggleVariables = { upstream: Upstream; enabled: boolean }

/**
 * Turns upstreams on or off by saving each in full. A row's switch updates at
 * once and only that row rolls back, with a toast, if the gateway refuses.
 * Returns `toggle` and the ids whose save is still running.
 */
function useToggleUpstream() {
  const { t } = useTranslation('gateway')
  const { connection, scope } = useGateway()
  const queryClient = useQueryClient()
  const queryKey = gatewayQueryKey(scope, 'upstreams')
  const mutationKey = [...queryKey, 'toggle']
  const setEnabled = (id: string, enabled: boolean) =>
    queryClient.setQueryData<Upstream[]>(queryKey, (list) =>
      list?.map((item) => (item.id === id ? { ...item, enabled } : item)),
    )
  const { mutate } = useMutation({
    mutationKey,
    mutationFn: ({ upstream, enabled }: ToggleVariables) =>
      saveUpstream(connection, upstream.id, {
        ...toUpstreamInput(upstream),
        enabled,
      }),
    onMutate: async ({ upstream, enabled }) => {
      await queryClient.cancelQueries({ queryKey })
      setEnabled(upstream.id, enabled)
    },
    onError: (error, { upstream }) => {
      // `upstream` is the row as it was before the switch was flipped.
      setEnabled(upstream.id, upstream.enabled)
      toastGatewayError(t, error)
    },
    // Refetch after the last running toggle only (this one still counts), so
    // the fresh list doesn't undo switches whose save hasn't finished.
    onSettled: () =>
      queryClient.isMutating({ mutationKey }) <= 1
        ? queryClient.invalidateQueries({ queryKey })
        : undefined,
  })
  const pending = useMutationState({
    filters: { mutationKey, status: 'pending' },
    select: (mutation) =>
      (mutation.state.variables as ToggleVariables).upstream.id,
  })
  return { toggle: mutate, pending: new Set(pending) }
}

/** Model providers the gateway forwards requests to. */
export function UpstreamsPage() {
  const { t } = useTranslation('gateway')
  const { connection, access, invalidate, scope, readOnly } = useGateway()
  const [importResult, setImportResult] =
    React.useState<VlmImportResult | null>(null)
  React.useEffect(() => setImportResult(null), [scope])
  const importer = useMutation({
    mutationFn: () => importVlm(connection),
    onSuccess: async (result) => {
      setImportResult(result)
      await invalidate('upstreams')
    },
    // A stale /access offered the import; the refreshed one hides it.
    onError: (error) =>
      toGatewayError(error).reason === 'vlm_import_forbidden'
        ? invalidate('access')
        : undefined,
  })
  const upstreams = useUpstreams()
  const keys = useKeys()
  const { toggle, pending: toggling } = useToggleUpstream()
  const [deleting, setDeleting] = React.useState<Upstream | null>(null)

  const rows = React.useMemo(
    () =>
      [...(upstreams.data ?? [])].sort((a, b) => a.name.localeCompare(b.name)),
    [upstreams.data],
  )
  const refreshing = upstreams.isFetching || keys.isFetching

  const addButton = !readOnly ? (
    <Button
      size="sm"
      nativeButton={false}
      render={
        <Link
          to="/gateway/upstreams/$upstreamId"
          params={{ upstreamId: NEW_ID }}
        />
      }
    >
      <PlusIcon />
      {t('upstreams.add')}
    </Button>
  ) : null
  const importButton = (empty = false) =>
    access?.can_import_vlm ? (
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={importer.isPending}
        title={t('upstreams.import.hint')}
        onClick={() => importer.mutate()}
      >
        {t(
          importer.isPending
            ? 'upstreams.import.running'
            : empty
              ? 'upstreams.import.useSettings'
              : 'upstreams.import.action',
        )}
      </Button>
    ) : null

  let body: React.ReactNode
  if (upstreams.isPending) {
    body = <LoadingState />
  } else if (upstreams.isError) {
    body = (
      <ErrorState
        title={t('upstreams.loadFailed')}
        error={upstreams.error}
        retrying={upstreams.isFetching}
        onRetry={() => void upstreams.refetch()}
      />
    )
  } else if (rows.length === 0) {
    body = (
      <EmptyState
        icon={<ServerIcon />}
        title={t('upstreams.empty.title')}
        description={t(
          readOnly ? 'upstreams.readOnly.empty' : 'upstreams.empty.description',
        )}
        action={
          <div className="flex flex-wrap gap-2">
            {importButton(true)}
            {addButton}
          </div>
        }
      />
    )
  } else {
    body = (
      <Table>
        <TableHeader>
          <TableRow className="bg-muted/20 hover:bg-muted/20">
            <TableHead className="pl-4">
              {t('upstreams.columns.name')}
            </TableHead>
            <TableHead className={DESKTOP_ONLY}>
              {t('upstreams.columns.protocol')}
            </TableHead>
            <TableHead className={DESKTOP_ONLY}>
              {t('upstreams.columns.provider')}
            </TableHead>
            <TableHead className={DESKTOP_ONLY}>
              {t('upstreams.columns.models')}
            </TableHead>
            <TableHead className={DESKTOP_ONLY}>
              {t('upstreams.columns.credentials')}
            </TableHead>
            <TableHead className={cn(DESKTOP_ONLY, 'text-right')}>
              {t('upstreams.columns.priority')}
            </TableHead>
            <TableHead className={DESKTOP_ONLY}>
              {t('upstreams.columns.usedBy')}
            </TableHead>
            <TableHead>{t('upstreams.columns.enabled')}</TableHead>
            <TableHead className="pr-4 text-right">
              {t('upstreams.columns.actions')}
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((upstream) => (
            <UpstreamRow
              key={upstream.id}
              upstream={upstream}
              readOnly={readOnly}
              usedBy={keysUsing(keys.data, { upstreamId: upstream.id })}
              toggling={toggling.has(upstream.id)}
              onToggle={(enabled) => toggle({ upstream, enabled })}
              onDelete={() => setDeleting(upstream)}
            />
          ))}
        </TableBody>
      </Table>
    )
  }

  return (
    <div className="flex w-full min-w-0 flex-col gap-5">
      <SectionHeader
        description={t('upstreams.description')}
        actions={
          <>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={refreshing}
              onClick={() => {
                void upstreams.refetch()
                void keys.refetch()
              }}
            >
              <RefreshCwIcon
                className={refreshing ? 'animate-spin' : undefined}
              />
              {t('actions.refresh')}
            </Button>
            {importButton()}
            {addButton}
          </>
        }
      />
      {importer.isError ? (
        <Notice
          tone="danger"
          action={
            toGatewayError(importer.error).reason === 'vlm_import_unavailable'
              ? addButton
              : undefined
          }
        >
          {gatewayErrorMessage(t, importer.error)}
        </Notice>
      ) : null}
      {importResult ? (
        <ImportResultNotice
          result={importResult}
          onDismiss={() => setImportResult(null)}
        />
      ) : null}
      <Card className="gap-0 overflow-hidden py-0">
        <CardContent className="p-0">{body}</CardContent>
      </Card>
      <DeleteUpstreamDialog
        upstream={deleting}
        onOpenChange={(open) => {
          if (!open) setDeleting(null)
        }}
      />
    </div>
  )
}

/** What an import of OpenViking's model settings saved, and what needs a look. */
function ImportResultNotice({
  result,
  onDismiss,
}: {
  result: VlmImportResult
  onDismiss: () => void
}) {
  const { t } = useTranslation('gateway')
  const { imported, needs_confirmation: confirm, skipped } = result
  // Upstreams awaiting Coding Plan confirmation are saved too.
  const saved = imported.length + confirm.length
  return (
    <Notice
      tone={confirm.length || skipped.length ? 'warning' : 'info'}
      action={
        <Button type="button" size="sm" variant="outline" onClick={onDismiss}>
          {t('upstreams.import.dismiss')}
        </Button>
      }
    >
      {saved ? (
        <p className="font-medium">
          {t('upstreams.import.summary', { count: saved })}
        </p>
      ) : skipped.length ? null : (
        <p className="font-medium">{t('upstreams.import.nothing')}</p>
      )}
      {confirm.length ? (
        <div>
          <p>{t('upstreams.import.needsConfirmation')}</p>
          <ul className="list-inside list-disc">
            {confirm.map((item) => (
              <li key={item.id}>
                {item.name}{' '}
                <Link
                  className="font-medium underline underline-offset-4"
                  to="/gateway/upstreams/$upstreamId"
                  params={{ upstreamId: item.id }}
                >
                  {t('upstreams.import.confirm')}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {skipped.length ? (
        <div>
          <p>{t('upstreams.import.skipped', { count: skipped.length })}</p>
          <ul className="list-inside list-disc">
            {skipped.map((item) => (
              <li key={item.id}>
                {item.id}:{' '}
                {t(`upstreams.import.reasons.${item.reason}`, {
                  defaultValue: t('upstreams.import.reasons.unknown'),
                })}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {saved ? <p className="text-xs">{t('upstreams.import.repeat')}</p> : null}
    </Notice>
  )
}

type UpstreamRowProps = {
  upstream: Upstream
  readOnly: boolean
  /** Keys that use this upstream; undefined while keys are loading. */
  usedBy: number | undefined
  toggling: boolean
  onToggle: (enabled: boolean) => void
  onDelete: () => void
}

function UpstreamRow({
  upstream,
  readOnly,
  usedBy,
  toggling,
  onToggle,
  onDelete,
}: UpstreamRowProps) {
  const { t, i18n } = useTranslation('gateway')
  const editLink = (
    <Link
      to="/gateway/upstreams/$upstreamId"
      params={{ upstreamId: upstream.id }}
    />
  )
  return (
    <TableRow>
      <TableCell className="pl-4 whitespace-normal">
        {/* Names wrap to two lines; the host shows in full unless very long. */}
        <div className="grid max-w-56 min-w-24 gap-0.5 md:min-w-40">
          <Link
            to="/gateway/upstreams/$upstreamId"
            params={{ upstreamId: upstream.id }}
            className="line-clamp-2 font-medium break-words hover:underline"
            title={upstream.name}
          >
            {upstream.name}
          </Link>
          <span
            className="max-w-full truncate font-mono text-xs text-muted-foreground md:w-max"
            title={upstream.base_url}
          >
            {hostFromUrl(upstream.base_url)}
          </span>
          <ProtocolBadge
            protocol={upstream.protocol}
            className="mt-0.5 md:hidden"
          />
        </div>
      </TableCell>
      <TableCell className={DESKTOP_ONLY}>
        <ProtocolBadge protocol={upstream.protocol} />
      </TableCell>
      <TableCell className={cn(DESKTOP_ONLY, 'text-sm')}>
        {upstream.vendor === 'generic' ? (
          <EmptyValue />
        ) : (
          vendorLabel(t, upstream.vendor)
        )}
      </TableCell>
      <TableCell className={DESKTOP_ONLY}>
        <ModelsSummary upstream={upstream} />
      </TableCell>
      <TableCell className={DESKTOP_ONLY}>
        <CredentialsSummary upstream={upstream} />
      </TableCell>
      <TableCell
        className={cn(
          DESKTOP_ONLY,
          'text-right font-mono text-xs tabular-nums',
        )}
      >
        {formatNumber(upstream.priority, i18n.resolvedLanguage)}
      </TableCell>
      <TableCell className={cn(DESKTOP_ONLY, 'text-sm')}>
        {usedBy === undefined ? (
          <EmptyValue />
        ) : usedBy === 0 ? (
          <span className="text-muted-foreground">
            {t('upstreams.notUsed')}
          </span>
        ) : (
          t('upstreams.usedBy', { count: usedBy })
        )}
      </TableCell>
      <TableCell>
        <Switch
          size="sm"
          checked={upstream.enabled}
          disabled={readOnly || toggling}
          aria-label={t(
            upstream.enabled
              ? 'upstreams.toggle.disable'
              : 'upstreams.toggle.enable',
            { name: upstream.name },
          )}
          onCheckedChange={(checked) => onToggle(checked)}
        />
      </TableCell>
      <TableCell className="pr-4">
        <div className="flex items-center justify-end gap-1">
          <UpstreamTest upstream={upstream} />
          {!readOnly ? (
            <>
              <Button
                size="sm"
                variant="ghost"
                className="h-8 px-2 text-xs"
                nativeButton={false}
                render={editLink}
              >
                <PencilIcon />
                <span className="max-md:sr-only">{t('actions.edit')}</span>
              </Button>
              <DropdownMenu>
                <DropdownMenuTrigger
                  render={
                    <Button
                      type="button"
                      size="icon-sm"
                      variant="ghost"
                      aria-label={t('actions.more')}
                      title={t('actions.more')}
                    />
                  }
                >
                  <EllipsisIcon />
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-auto min-w-48">
                  <DropdownMenuItem
                    variant="destructive"
                    className="items-start"
                    onClick={onDelete}
                  >
                    <Trash2Icon className="mt-0.5" />
                    <span className="grid gap-0.5">
                      <span>{t('upstreams.delete.action')}</span>
                      {usedBy ? (
                        <span className="text-xs">
                          {t('upstreams.delete.blocked', { count: usedBy })}
                        </span>
                      ) : null}
                    </span>
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </>
          ) : null}
        </div>
      </TableCell>
    </TableRow>
  )
}

/** Up to three model chips, "+N" for the rest, or "Any model"; alias count after. */
function ModelsSummary({ upstream }: { upstream: Upstream }) {
  const { t } = useTranslation('gateway')
  const { models, aliases } = upstream
  const shown = models.slice(0, VISIBLE_MODELS)
  const hidden = models.slice(VISIBLE_MODELS)
  const aliasNames = Object.keys(aliases)
  return (
    <div className="flex max-w-80 flex-wrap items-center gap-1">
      {models.length === 0 ? (
        <span className="text-xs text-muted-foreground">
          {t('upstreams.models.any')}
        </span>
      ) : (
        shown.map((model) => (
          <Badge
            key={model}
            variant="secondary"
            className="max-w-40 font-mono font-normal"
            title={model}
          >
            <span className="truncate">{model}</span>
          </Badge>
        ))
      )}
      {hidden.length ? (
        <Badge
          variant="outline"
          className="font-normal text-muted-foreground"
          title={hidden.join(', ')}
        >
          {t('upstreams.models.more', { count: hidden.length })}
        </Badge>
      ) : null}
      {aliasNames.length ? (
        <span
          className="text-xs text-muted-foreground"
          title={aliasNames
            .map((name) => `${name} → ${aliases[name]}`)
            .join('\n')}
        >
          {t('upstreams.models.aliases', { count: aliasNames.length })}
        </span>
      ) : null}
    </div>
  )
}

/** Who provides the provider key, with warnings that block every request. */
function CredentialsSummary({ upstream }: { upstream: Upstream }) {
  const { t } = useTranslation('gateway')
  const keyMissing = upstream.auth_mode === 'managed' && !upstream.has_api_key
  const blocked = upstream.coding_plan && !upstream.allow_coding_plan
  return (
    <div className="flex flex-col items-start gap-1">
      {keyMissing ? (
        <ToneBadge
          tone="warning"
          title={t('upstreams.credentials.keyMissingHint')}
        >
          <TriangleAlertIcon />
          {t('upstreams.credentials.keyMissing')}
        </ToneBadge>
      ) : (
        <span className="text-sm">{authModeLabel(t, upstream.auth_mode)}</span>
      )}
      {blocked ? (
        <ToneBadge tone="danger" title={t('upstreams.credentials.blockedHint')}>
          <TriangleAlertIcon />
          {t('upstreams.credentials.blocked')}
        </ToneBadge>
      ) : null}
    </div>
  )
}
