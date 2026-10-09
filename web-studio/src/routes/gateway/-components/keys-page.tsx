import * as React from 'react'
import { useMutation } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import {
  EllipsisIcon,
  KeyRoundIcon,
  PlusIcon,
  RefreshCwIcon,
  ServerIcon,
  SlidersHorizontalIcon,
  Trash2Icon,
  UserXIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { Badge } from '#/components/ui/badge'
import { Button } from '#/components/ui/button'
import { Card, CardContent } from '#/components/ui/card'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '#/components/ui/dropdown-menu'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '#/components/ui/table'
import { cn } from '#/lib/utils'

import { deleteUserData, revokeKey } from '../-lib/api'
import type { GatewayKey, IssuedKey, Profile, Upstream } from '../-lib/api'
import { formatDateTime, formatRelativeTime, shortenId } from '../-lib/format'
import { gatewayErrorMessage, protocolLabel } from '../-lib/localize'
import { NEW_ID } from '../-lib/search'
import {
  useConnectionInfo,
  useGateway,
  useKeys,
  useProfiles,
  useUpstreams,
} from '../-lib/use-gateway'
import { ConfirmDialog } from './confirm-dialog'
import { EmptyState, ErrorState, LoadingState } from './empty-state'
import { ExplainedButton } from './explained-button'
import { KeysIssueDialog } from './keys-issue-dialog'
import { KeysSecretDialog } from './keys-secret-dialog'
import { SectionHeader } from './section-header'
import { ToneBadge, protocolTint } from './status-badges'

/** Chips shown per cell before the rest collapse into "+N". */
const CHIP_LIMIT = 3

/**
 * Columns left out below `md`, so a row keeps its name and actions on screen;
 * the name cell then shows the key prefix and user instead.
 */
const DESKTOP_ONLY = 'hidden md:table-cell'

/** What has to exist before a key can be issued. */
type Missing = 'upstream' | 'profile' | 'both'

/** Issue and revoke gateway keys. */
export function KeysPage() {
  const { t } = useTranslation('gateway')
  const { connection, invalidate } = useGateway()
  const keysQuery = useKeys()
  const profilesQuery = useProfiles()
  const upstreamsQuery = useUpstreams()
  const connectionQuery = useConnectionInfo()

  const [issueOpen, setIssueOpen] = React.useState(false)
  // Remounts the issue form with empty fields each time it opens.
  const [issueSession, setIssueSession] = React.useState(0)
  const [issued, setIssued] = React.useState<IssuedKey | null>(null)
  const [secretOpen, setSecretOpen] = React.useState(false)
  const [revokeTarget, setRevokeTarget] = React.useState<GatewayKey | null>(
    null,
  )
  /** OpenViking user whose gateway data is about to be deleted. */
  const [userTarget, setUserTarget] = React.useState<string | null>(null)

  const revoke = useMutation({
    mutationFn: (key: GatewayKey) => revokeKey(connection, key.id),
    onSuccess: async (_result, key) => {
      toast.success(t('keys.revoke.done', { name: key.name }))
      await invalidate('keys', 'overview', 'tools')
    },
    onError: (error) => toast.error(gatewayErrorMessage(t, error)),
  })
  const deleteUser = useMutation({
    mutationFn: (userId: string) => deleteUserData(connection, userId),
    onSuccess: async (_result, userId) => {
      toast.success(t('keys.deleteUser.done', { user: userId }))
      await invalidate('keys', 'overview', 'logs')
    },
    onError: (error) => toast.error(gatewayErrorMessage(t, error)),
  })

  const keys = [...(keysQuery.data ?? [])].sort(
    (a, b) => b.created_at - a.created_at,
  )
  const profiles = profilesQuery.data ?? []
  const upstreams = upstreamsQuery.data ?? []
  const listsReady = profilesQuery.isSuccess && upstreamsQuery.isSuccess
  const missing: Missing | null = !listsReady
    ? null
    : !upstreams.length && !profiles.length
      ? 'both'
      : !upstreams.length
        ? 'upstream'
        : !profiles.length
          ? 'profile'
          : null
  const canIssue = listsReady && !missing
  const refreshing =
    keysQuery.isFetching ||
    profilesQuery.isFetching ||
    upstreamsQuery.isFetching
  const userKeyCount = keys.filter((key) => key.user_id === userTarget).length

  function openIssue() {
    setIssueSession((session) => session + 1)
    setIssueOpen(true)
  }

  function refresh() {
    void keysQuery.refetch()
    void profilesQuery.refetch()
    void upstreamsQuery.refetch()
  }

  const issueButton = (
    <IssueButton
      disabled={!canIssue}
      reason={missing ? t(`keys.prerequisites.${missing}`) : undefined}
      onClick={openIssue}
    />
  )

  return (
    <div className="flex w-full min-w-0 flex-col gap-5">
      <SectionHeader
        description={t('keys.description')}
        actions={
          <>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={refreshing}
              onClick={refresh}
            >
              <RefreshCwIcon
                className={refreshing ? 'animate-spin' : undefined}
              />
              {t('actions.refresh')}
            </Button>
            {/* The empty state carries its own Issue button. */}
            {keysQuery.isSuccess && !keys.length ? null : issueButton}
          </>
        }
      />

      <Card className="overflow-hidden py-0">
        <CardContent className="p-0">
          {keysQuery.isPending ? (
            <LoadingState />
          ) : keysQuery.isError ? (
            <ErrorState
              title={t('keys.loadFailed')}
              error={keysQuery.error}
              retrying={keysQuery.isFetching}
              onRetry={() => void keysQuery.refetch()}
            />
          ) : keys.length === 0 ? (
            <EmptyState
              icon={<KeyRoundIcon />}
              title={t('keys.empty.title')}
              description={
                <>
                  <span className="block">{t('keys.empty.description')}</span>
                  {missing ? (
                    <span className="mt-2 block font-medium text-foreground">
                      {t(`keys.prerequisites.${missing}`)}
                    </span>
                  ) : null}
                </>
              }
              action={
                <>
                  {missing === 'upstream' || missing === 'both' ? (
                    <Button
                      variant="outline"
                      size="sm"
                      nativeButton={false}
                      render={
                        <Link
                          to="/gateway/upstreams/$upstreamId"
                          params={{ upstreamId: NEW_ID }}
                        />
                      }
                    >
                      <ServerIcon />
                      {t('keys.prerequisites.addUpstream')}
                    </Button>
                  ) : null}
                  {missing === 'profile' || missing === 'both' ? (
                    <Button
                      variant="outline"
                      size="sm"
                      nativeButton={false}
                      render={
                        <Link
                          to="/gateway/profiles/$profileId"
                          params={{ profileId: NEW_ID }}
                        />
                      }
                    >
                      <SlidersHorizontalIcon />
                      {t('keys.prerequisites.createProfile')}
                    </Button>
                  ) : null}
                  {issueButton}
                </>
              }
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow className="bg-muted/20 hover:bg-muted/20">
                  <TableHead className="pl-4">{t('keys.table.name')}</TableHead>
                  <TableHead className={DESKTOP_ONLY}>
                    {t('keys.table.key')}
                  </TableHead>
                  <TableHead className={DESKTOP_ONLY}>
                    {t('keys.table.user')}
                  </TableHead>
                  <TableHead className={DESKTOP_ONLY}>
                    {t('keys.table.profile')}
                  </TableHead>
                  <TableHead className={DESKTOP_ONLY}>
                    {t('keys.table.upstreams')}
                  </TableHead>
                  <TableHead className={DESKTOP_ONLY}>
                    {t('keys.table.models')}
                  </TableHead>
                  <TableHead className={DESKTOP_ONLY}>
                    {t('keys.table.created')}
                  </TableHead>
                  <TableHead className="pr-4 text-right">
                    {t('keys.table.actions')}
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {keys.map((key) => (
                  <KeyRow
                    key={key.id}
                    gatewayKey={key}
                    profiles={profilesQuery.isSuccess ? profiles : undefined}
                    upstreams={upstreamsQuery.isSuccess ? upstreams : undefined}
                    onRevoke={() => setRevokeTarget(key)}
                    onDeleteUser={() => setUserTarget(key.user_id)}
                  />
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <KeysIssueDialog
        key={issueSession}
        open={issueOpen}
        onOpenChange={setIssueOpen}
        profiles={profiles}
        upstreams={upstreams}
        onIssued={(key) => {
          setIssueOpen(false)
          setIssued(key)
          setSecretOpen(true)
        }}
      />

      <KeysSecretDialog
        open={secretOpen}
        issued={issued}
        baseUrl={connectionQuery.data?.base_url ?? ''}
        profileName={
          profiles.find((profile) => profile.id === issued?.policy_id)?.name
        }
        upstreams={upstreams}
        onDone={() => setSecretOpen(false)}
        onClosed={() => setIssued(null)}
      />

      <ConfirmDialog
        open={revokeTarget !== null}
        onOpenChange={(open) => {
          if (!open) setRevokeTarget(null)
        }}
        title={t('keys.revoke.title', { name: revokeTarget?.name ?? '' })}
        description={t('keys.revoke.description')}
        confirmLabel={t('keys.revoke.confirm')}
        icon={<Trash2Icon />}
        onConfirm={() =>
          revokeTarget ? revoke.mutateAsync(revokeTarget) : undefined
        }
      />

      <ConfirmDialog
        open={userTarget !== null}
        onOpenChange={(open) => {
          if (!open) setUserTarget(null)
        }}
        title={t('keys.deleteUser.title', { user: userTarget ?? '' })}
        description={t('keys.deleteUser.description', {
          user: userTarget ?? '',
          count: userKeyCount,
        })}
        confirmLabel={t('keys.deleteUser.confirm')}
        icon={<UserXIcon />}
        onConfirm={() =>
          userTarget !== null ? deleteUser.mutateAsync(userTarget) : undefined
        }
      />
    </div>
  )
}

/** Primary "Issue key"; when disabled, a tooltip says what is missing. */
function IssueButton({
  disabled,
  reason,
  onClick,
}: {
  disabled: boolean
  reason?: string
  onClick: () => void
}) {
  const { t } = useTranslation('gateway')
  return (
    <ExplainedButton
      type="button"
      size="sm"
      disabled={disabled}
      explanation={disabled ? reason : undefined}
      onClick={onClick}
    >
      <PlusIcon />
      {t('keys.issue')}
    </ExplainedButton>
  )
}

/** Up to `CHIP_LIMIT` chips, then a "+N" chip listing the rest on hover. */
function ChipList<T>({
  items,
  render,
  label,
}: {
  items: T[]
  render: (item: T) => React.ReactNode
  label: (item: T) => string
}) {
  const { t } = useTranslation('gateway')
  const shown = items.slice(0, CHIP_LIMIT)
  const rest = items.slice(CHIP_LIMIT)
  return (
    <div className="flex max-w-72 flex-wrap items-center gap-1">
      {shown.map(render)}
      {rest.length ? (
        <Badge
          variant="outline"
          className="font-normal text-muted-foreground"
          title={rest.map(label).join(', ')}
        >
          {t('keys.extra', { count: rest.length })}
        </Badge>
      ) : null}
    </div>
  )
}

function KeyRow({
  gatewayKey: key,
  profiles,
  upstreams,
  onRevoke,
  onDeleteUser,
}: {
  gatewayKey: GatewayKey
  /** Undefined while the list isn't loaded, so nothing shows as missing. */
  profiles?: Profile[]
  upstreams?: Upstream[]
  onRevoke: () => void
  onDeleteUser: () => void
}) {
  const { t, i18n } = useTranslation('gateway')
  const locale = i18n.resolvedLanguage
  const profile = profiles?.find((item) => item.id === key.policy_id)

  return (
    <TableRow>
      <TableCell className="pl-4">
        <span className="block max-w-48 truncate font-medium" title={key.name}>
          {key.name}
        </span>
        <span
          className="block max-w-56 truncate font-mono text-xs text-muted-foreground md:hidden"
          title={key.user_id}
        >
          {`${key.prefix}… · ${key.user_id}`}
        </span>
      </TableCell>
      <TableCell
        className={cn(DESKTOP_ONLY, 'font-mono text-xs text-muted-foreground')}
      >
        {`${key.prefix}…`}
      </TableCell>
      <TableCell className={DESKTOP_ONLY}>
        <span
          className="block max-w-40 truncate font-mono text-xs"
          title={key.user_id}
        >
          {key.user_id}
        </span>
      </TableCell>
      <TableCell className={DESKTOP_ONLY}>
        {profile ? (
          <Link
            to="/gateway/profiles/$profileId"
            params={{ profileId: profile.id }}
            className="block max-w-48 truncate underline-offset-4 hover:underline"
            title={profile.name}
          >
            {profile.name}
          </Link>
        ) : profiles ? (
          <ToneBadge tone="warning" title={t('keys.missingProfile')}>
            {t('keys.missing')}
          </ToneBadge>
        ) : (
          <span className="font-mono text-xs text-muted-foreground">
            {shortenId(key.policy_id)}
          </span>
        )}
      </TableCell>
      <TableCell className={DESKTOP_ONLY}>
        <ChipList
          items={key.upstream_ids}
          label={(id) =>
            upstreams?.find((upstream) => upstream.id === id)?.name ?? id
          }
          render={(id) => {
            const upstream = upstreams?.find((item) => item.id === id)
            if (upstream) {
              return (
                <Badge
                  key={id}
                  variant="outline"
                  className={cn(
                    'max-w-40 font-normal',
                    protocolTint(upstream.protocol),
                  )}
                  title={protocolLabel(t, upstream.protocol)}
                >
                  <span className="truncate">{upstream.name}</span>
                </Badge>
              )
            }
            return upstreams ? (
              <ToneBadge
                key={id}
                tone="warning"
                title={t('keys.missingUpstream')}
              >
                {t('keys.missing')}
              </ToneBadge>
            ) : (
              <Badge
                key={id}
                variant="outline"
                className="font-mono font-normal text-muted-foreground"
              >
                {shortenId(id)}
              </Badge>
            )
          }}
        />
      </TableCell>
      <TableCell className={DESKTOP_ONLY}>
        {key.models.length ? (
          <ChipList
            items={key.models}
            label={(model) => model}
            render={(model) => (
              <Badge
                key={model}
                variant="secondary"
                className="max-w-40 font-mono font-normal"
                title={model}
              >
                <span className="truncate">{model}</span>
              </Badge>
            )}
          />
        ) : (
          <span className="text-muted-foreground">{t('states.any')}</span>
        )}
      </TableCell>
      <TableCell className={cn(DESKTOP_ONLY, 'text-xs text-muted-foreground')}>
        <span title={formatDateTime(key.created_at, locale)}>
          {formatRelativeTime(key.created_at, locale)}
        </span>
      </TableCell>
      <TableCell className="pr-4">
        <div className="flex items-center justify-end gap-1">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-8 px-2 text-xs text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
            aria-label={t('keys.actions.revokeKey', { name: key.name })}
            onClick={onRevoke}
          >
            <Trash2Icon />
            {t('actions.revoke')}
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger
              render={
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  className="text-muted-foreground"
                  aria-label={t('keys.actions.more', { name: key.name })}
                />
              }
            >
              <EllipsisIcon />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-auto min-w-48">
              <DropdownMenuItem variant="destructive" onClick={onDeleteUser}>
                <UserXIcon />
                {t('keys.actions.deleteUserData')}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </TableCell>
    </TableRow>
  )
}
