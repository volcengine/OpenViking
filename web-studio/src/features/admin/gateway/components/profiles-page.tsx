import { Notice } from '#/features/admin/gateway/components/notice'
import { useMutation } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import {
  CopyPlusIcon,
  EyeIcon,
  KeyRoundIcon,
  PencilIcon,
  PlusIcon,
  RefreshCwIcon,
  SlidersHorizontalIcon,
  Trash2Icon,
} from 'lucide-react'
import * as React from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { cn } from '#/shared/lib/utils'
import { Button } from '#/shared/ui/button'
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from '#/shared/ui/card'

import { ConfirmDialog } from '#/features/admin/gateway/components/confirm-dialog'
import { DocsLink } from '#/features/admin/gateway/components/docs-link'
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '#/features/admin/gateway/components/empty-state'
import { ExplainedButton } from '#/features/admin/gateway/components/explained-button'
import type { ProfileSection } from '#/features/admin/gateway/components/profiles-settings'
import {
  PROFILE_SECTIONS,
  isSectionOn,
} from '#/features/admin/gateway/components/profiles-settings'
import { RecommendedProfileButton } from '#/features/admin/gateway/components/recommended-profile-button'
import { SectionHeader } from '#/features/admin/gateway/components/section-header'
import type { GatewayTool, Profile } from '#/features/admin/gateway/lib/api'
import {
  deleteProfile,
  keysUsing,
  toGatewayError,
} from '#/features/admin/gateway/lib/api'
import { toastGatewayError } from '#/features/admin/gateway/lib/error-toast'
import {
  EMPTY_VALUE,
  formatNumber,
  formatPercent,
} from '#/features/admin/gateway/lib/format'
import type { Translate } from '#/features/admin/gateway/lib/localize'
import {
  offeredTools,
  toProfileSettings,
} from '#/features/admin/gateway/lib/profile-schema'
import { NEW_ID } from '#/features/admin/gateway/lib/search'
import {
  useGateway,
  useKeys,
  useProfiles,
  useTools,
} from '#/features/admin/gateway/lib/use-gateway'

/** One-line state of each section for a profile card, and whether it is on. */
function summarize(
  t: Translate,
  profile: Profile,
  tools: GatewayTool[] | undefined,
  locale?: string,
): Record<ProfileSection, { text: string; on: boolean }> {
  const settings = toProfileSettings(profile)
  const state = (on: boolean, text: string) => ({
    on,
    text: on ? text : t('states.off'),
  })
  return {
    recall: state(
      settings.recall,
      t('profiles.summary.recallOn', {
        tokens: formatNumber(settings.max_tokens, locale),
      }),
    ),
    capture: state(settings.capture, t('states.on')),
    longConversations: state(
      isSectionOn(settings, 'longConversations'),
      [
        settings.compaction &&
          t('profiles.summary.compactionOn', {
            percent: formatPercent(settings.compaction_threshold, locale),
          }),
        settings.agent_windows && t('profiles.summary.agentWindowsOn'),
      ]
        .filter(Boolean)
        .join(' · '),
    ),
    tools: state(
      settings.gateway_tools,
      tools === undefined
        ? t('states.on')
        : t('profiles.summary.toolsEnabled', {
            count: offeredTools(settings, tools).length,
          }),
    ),
  }
}

function ProfileCard({
  profile,
  usedBy,
  tools,
  onDelete,
  readOnly,
}: {
  profile: Profile
  /** Keys using the profile; undefined while keys are loading. */
  usedBy?: number
  tools: GatewayTool[] | undefined
  readOnly: boolean
  onDelete: () => void
}) {
  const { t, i18n } = useTranslation('gateway')
  const summary = summarize(t, profile, tools, i18n.resolvedLanguage)
  const inUse = Boolean(usedBy)
  const deleteHint = inUse
    ? t('profiles.deleteBlocked', { count: usedBy })
    : t('actions.delete')

  return (
    <Card className="gap-0 py-0">
      <CardHeader className="border-b px-5 py-4 [.border-b]:pb-4">
        <CardTitle className="min-w-0 truncate">
          <Link
            to="/admin/gateway/profiles/$profileId"
            params={{ profileId: profile.id }}
            title={profile.name}
            className="rounded-sm hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          >
            {profile.name}
          </Link>
        </CardTitle>
      </CardHeader>
      <CardContent className="flex-1 px-5 py-4">
        <dl className="grid gap-2.5">
          {PROFILE_SECTIONS.map(({ id, icon: Icon }) => (
            <div key={id} className="flex min-w-0 items-start gap-3">
              <dt className="flex shrink-0 items-center gap-2 text-muted-foreground">
                <Icon className="size-4" />
                {t(`profiles.${id}.title`)}
              </dt>
              <dd
                className={cn(
                  'ml-auto min-w-0 text-right',
                  summary[id].on ? 'text-foreground' : 'text-muted-foreground',
                )}
              >
                {summary[id].text}
              </dd>
            </div>
          ))}
        </dl>
      </CardContent>
      <CardFooter className="flex-wrap justify-between gap-2 border-t bg-muted/20 px-5 py-2 [.border-t]:pt-2">
        <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <KeyRoundIcon className="size-3.5" />
          {usedBy === undefined
            ? EMPTY_VALUE
            : usedBy > 0
              ? t('profiles.usedBy', { count: usedBy })
              : t('profiles.unused')}
        </span>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="sm"
            className="h-8 px-2 text-xs"
            nativeButton={false}
            render={
              <Link
                to="/admin/gateway/profiles/$profileId"
                params={{ profileId: profile.id }}
              />
            }
          >
            {readOnly ? <EyeIcon /> : <PencilIcon />}
            {t(readOnly ? 'actions.view' : 'actions.edit')}
          </Button>
          {!readOnly ? (
            <>
              <Button
                variant="ghost"
                size="sm"
                className="h-8 px-2 text-xs"
                nativeButton={false}
                render={
                  <Link
                    to="/admin/gateway/profiles/$profileId"
                    params={{ profileId: NEW_ID }}
                    search={{ from: profile.id }}
                  />
                }
              >
                <CopyPlusIcon />
                {t('actions.duplicate')}
              </Button>
              <ExplainedButton
                type="button"
                variant="ghost"
                size="icon-sm"
                disabled={inUse}
                explanation={deleteHint}
                aria-label={t('actions.delete')}
                className="text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                onClick={onDelete}
              >
                <Trash2Icon />
              </ExplainedButton>
            </>
          ) : null}
        </div>
      </CardFooter>
    </Card>
  )
}

/** Context profiles: recall, saving, long conversations and tools. */
export function ProfilesPage() {
  const { t } = useTranslation('gateway')
  const { connection, invalidate, readOnly } = useGateway()
  const [inUseCount, setInUseCount] = React.useState<number | null>(null)
  const profiles = useProfiles()
  const keys = useKeys()
  const tools = useTools({
    enabled: profiles.data?.some((profile) => profile.gateway_tools) ?? false,
  })
  const [deleteTarget, setDeleteTarget] = React.useState<Profile | null>(null)

  const sorted = React.useMemo(
    () =>
      [...(profiles.data ?? [])].sort(
        (a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id),
      ),
    [profiles.data],
  )

  const remove = useMutation({
    mutationFn: (profile: Profile) => deleteProfile(connection, profile.id),
    onSuccess: async (_result, profile) => {
      setInUseCount(null)
      toast.success(t('profiles.toast.deleted', { name: profile.name }))
      await invalidate('profiles')
    },
    // A profile still in use gets the notice with its next steps instead.
    onError: (error) => {
      const failure = toGatewayError(error)
      if (failure.reason === 'in_use') setInUseCount(failure.keyCount ?? 0)
      else toastGatewayError(t, error)
    },
  })

  const refreshing = profiles.isFetching || keys.isFetching || tools.isFetching

  let content: React.ReactNode
  if (profiles.isPending) {
    content = (
      <Card className="py-0">
        <LoadingState />
      </Card>
    )
  } else if (profiles.isError) {
    content = (
      <Card className="py-0">
        <ErrorState
          title={t('profiles.loadFailed')}
          error={profiles.error}
          retrying={profiles.isFetching}
          onRetry={() => void profiles.refetch()}
        />
      </Card>
    )
  } else if (sorted.length === 0) {
    content = (
      <Card className="py-0">
        <EmptyState
          icon={<SlidersHorizontalIcon />}
          title={t('profiles.empty.title')}
          description={t(
            readOnly ? 'profiles.empty.readOnly' : 'profiles.empty.description',
          )}
          action={
            readOnly ? undefined : (
              <>
                <RecommendedProfileButton />
                <Button
                  variant="outline"
                  size="sm"
                  nativeButton={false}
                  render={
                    <Link
                      to="/admin/gateway/profiles/$profileId"
                      params={{ profileId: NEW_ID }}
                    />
                  }
                >
                  <SlidersHorizontalIcon />
                  {t('profiles.actions.customize')}
                </Button>
              </>
            )
          }
        />
      </Card>
    )
  } else {
    content = (
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {sorted.map((profile) => (
          <ProfileCard
            key={profile.id}
            profile={profile}
            readOnly={readOnly}
            tools={tools.data}
            usedBy={keysUsing(keys.data, { profileId: profile.id })}
            onDelete={() => setDeleteTarget(profile)}
          />
        ))}
      </div>
    )
  }

  return (
    <div className="flex w-full min-w-0 flex-col gap-5">
      <SectionHeader
        description={t('profiles.description')}
        actions={
          <>
            <DocsLink section="profiles" />
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={refreshing}
              onClick={() => void invalidate('profiles', 'keys', 'tools')}
            >
              <RefreshCwIcon
                className={refreshing ? 'animate-spin' : undefined}
              />
              {t('actions.refresh')}
            </Button>
            {!readOnly ? (
              <Button
                size="sm"
                nativeButton={false}
                render={
                  <Link
                    to="/admin/gateway/profiles/$profileId"
                    params={{ profileId: NEW_ID }}
                  />
                }
              >
                <PlusIcon />
                {t('profiles.actions.new')}
              </Button>
            ) : null}
          </>
        }
      />
      {tools.isError ? (
        <Card className="py-0">
          <ErrorState
            className="min-h-24"
            title={t('profiles.tools.loadFailed')}
            error={tools.error}
            retrying={tools.isFetching}
            onRetry={() => void tools.refetch()}
          />
        </Card>
      ) : null}
      {inUseCount !== null ? (
        <Notice
          tone="warning"
          action={
            <Button
              variant="outline"
              size="sm"
              nativeButton={false}
              render={<Link to="/admin/gateway/keys" />}
            >
              {t('actions.viewKeys')}
            </Button>
          }
        >
          <p>{t('errors.reasons.in_use', { count: inUseCount })}</p>
          <p>{t('profiles.inUseHelp')}</p>
        </Notice>
      ) : null}
      {content}
      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null)
        }}
        title={t('profiles.deleteDialog.title', {
          name: deleteTarget?.name ?? '',
        })}
        description={t('profiles.deleteDialog.description')}
        confirmLabel={t('profiles.deleteDialog.confirm')}
        icon={<Trash2Icon />}
        onConfirm={() =>
          deleteTarget ? remove.mutateAsync(deleteTarget) : undefined
        }
      />
    </div>
  )
}
