import * as React from 'react'
import { useMutation } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import {
  ArrowRightIcon,
  CheckIcon,
  DownloadIcon,
  KeyRoundIcon,
  ListChecksIcon,
  LoaderCircleIcon,
  TriangleAlertIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { cn } from '#/shared/lib/utils'
import { Button } from '#/shared/ui/button'
import {
  Card,
  CardAction,
  CardContent,
  CardHeader,
  CardTitle,
} from '#/shared/ui/card'
import { copyTextToClipboard } from '#/shared/lib/clipboard'

import { issueKey } from '#/features/admin/gateway/lib/api'
import type {
  IssuedKey,
  KeyRequest,
  Upstream,
  VlmImportResult,
} from '#/features/admin/gateway/lib/api'
import { toastGatewayError } from '#/features/admin/gateway/lib/error-toast'
import {
  isQuickUsable,
  quickPlan,
  quickProfile,
} from '#/features/admin/gateway/lib/quick-start'
import { NEW_ID } from '#/features/admin/gateway/lib/search'
import {
  useConnectionInfo,
  useGateway,
  useKeys,
  useProfiles,
  useUpstreams,
} from '#/features/admin/gateway/lib/use-gateway'
import { ManagementUnlockButton } from '#/features/admin/gateway/components/access-notices'
import { KeysIssueDialog } from '#/features/admin/gateway/components/keys-issue-dialog'
import { KeysSecretDialog } from '#/features/admin/gateway/components/keys-secret-dialog'
import { OverviewQuickStart } from '#/features/admin/gateway/components/overview-quick-start'
import { RecommendedProfileButton } from '#/features/admin/gateway/components/recommended-profile-button'
import {
  ImportResultNotice,
  useVlmImport,
} from '#/features/admin/gateway/components/upstreams-page'

type StepState = 'done' | 'current' | 'todo'

function StepMarker({ number, state }: { number: number; state: StepState }) {
  const { t } = useTranslation('gateway')
  if (state === 'done') {
    return (
      <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-emerald-500/15 text-emerald-700 dark:text-emerald-300">
        <CheckIcon className="size-3.5" />
        <span className="sr-only">{t('overview.setup.done')}</span>
      </span>
    )
  }
  return (
    <span
      className={cn(
        'flex size-6 shrink-0 items-center justify-center rounded-full text-xs font-medium tabular-nums',
        state === 'current'
          ? 'bg-primary text-primary-foreground'
          : 'border border-dashed border-muted-foreground/40 text-muted-foreground',
      )}
    >
      {number}
    </span>
  )
}

type SetupStepProps = {
  number: number
  state: StepState
  title: string
  description: React.ReactNode
  children: React.ReactNode
}

function SetupStep({
  number,
  state,
  title,
  description,
  children,
}: SetupStepProps) {
  return (
    <li
      className={cn(
        'flex min-w-0 flex-col gap-3 rounded-lg border bg-background/70 p-4',
        state === 'current' && 'border-primary/40 shadow-xs',
      )}
    >
      <div className="flex items-center gap-2.5">
        <StepMarker number={number} state={state} />
        <p
          className={cn(
            'font-medium',
            state === 'done' && 'text-muted-foreground',
          )}
        >
          {title}
        </p>
      </div>
      <div className="text-sm leading-6 text-muted-foreground">
        {description}
      </div>
      <div className="mt-auto flex flex-col items-start gap-1">{children}</div>
    </li>
  )
}

/** A step's way around its one-click action, e.g. the full form. */
function StepLink(props: React.ComponentProps<typeof Button>) {
  return (
    <Button
      size="sm"
      variant="link"
      className="h-auto px-0 text-muted-foreground hover:text-foreground"
      {...props}
    />
  )
}

/** Whether an import saved something that still needs a look. */
function needsReview(result: VlmImportResult) {
  return (
    result.needs_confirmation.length > 0 ||
    result.skipped.length > 0 ||
    result.imported.length === 0
  )
}

type QuickIssued = {
  issued: IssuedKey
  profileName?: string
  upstreams: Upstream[]
}

/**
 * Checklist from an empty gateway to the first request: add an upstream,
 * create a context profile, issue a key, connect a client. Each of the first
 * three steps runs in place with one click (import OpenViking's model
 * settings, create the recommended profile, issue a key for yourself), with a
 * link to the full form beside it. A step is done once its list is non-empty.
 * Before the first key, quick mode does the first three steps in one click.
 */
export function OverviewSetup() {
  const { t } = useTranslation('gateway')
  const { connection, access, readOnly, invalidate } = useGateway()
  const { isGatewayHost } = useAppConnection()
  const upstreams = useUpstreams()
  const profiles = useProfiles()
  const keys = useKeys()
  const connectionInfo = useConnectionInfo()
  const vlmImport = useVlmImport({
    onSuccess: (result) => {
      if (!needsReview(result))
        toast.success(
          t('upstreams.import.summary', { count: result.imported.length }),
        )
    },
    onError: (error) => toastGatewayError(t, error),
  })
  const importing = vlmImport.importer.isPending
  const plan = quickPlan({
    keys: keys.data,
    upstreams: upstreams.data,
    profiles: profiles.data,
    access,
    readOnly,
    gatewayHost: isGatewayHost,
  })
  // Dialogs live here: the quick-mode card goes away once a key exists.
  const [issueDefaults, setIssueDefaults] = React.useState<KeyRequest | null>(
    null,
  )
  const [issueSession, setIssueSession] = React.useState(0)
  const [issued, setIssued] = React.useState<QuickIssued | null>(null)
  const [secretOpen, setSecretOpen] = React.useState(false)
  const showSecret = (next: QuickIssued) => {
    setIssued(next)
    setSecretOpen(true)
  }
  const openIssueDialog = (defaults: KeyRequest) => {
    setIssueSession((session) => session + 1)
    setIssueDefaults(defaults)
  }

  const baseUrl = connectionInfo.data?.base_url
  const copyAddress = async () => {
    try {
      await copyTextToClipboard(baseUrl ?? '')
      toast.success(t('copy.done'))
    } catch {
      toast.error(t('copy.failed'))
    }
  }

  // Step 3 issues the key quick mode would: the preferred profile and every
  // upstream usable without further setup.
  const keyProfile = quickProfile(profiles.data ?? [])
  const usableUpstreams = (upstreams.data ?? []).filter(isQuickUsable)
  const keyRequest: KeyRequest = {
    name: t('overview.setup.key.name'),
    policy_id: keyProfile?.id ?? '',
    upstream_ids: usableUpstreams.map((upstream) => upstream.id),
    models: [],
  }
  const issue = useMutation({
    mutationFn: (request: KeyRequest) => issueKey(connection, request),
    onSuccess: async (key) => {
      showSecret({
        issued: key,
        profileName: keyProfile?.name,
        upstreams: upstreams.data ?? [],
      })
      await invalidate('keys', 'overview', 'tools')
    },
    onError: (error) => toastGatewayError(t, error),
  })
  // Root picks the key's user, and without a usable upstream the form lets
  // you pick one by hand; otherwise the key is issued right away.
  const issueNow = () =>
    plan.pickUser || !usableUpstreams.length
      ? openIssueDialog(keyRequest)
      : issue.mutate(keyRequest)

  const counts = [upstreams.data, profiles.data, keys.data].map(
    (list) => list?.length,
  )
  const loaded = counts.every((count) => count !== undefined)
  const done = [...counts.map((count) => Boolean(count)), false]
  const current = loaded ? done.indexOf(false) : -1
  const stateOf = (index: number): StepState =>
    done[index]
      ? 'done'
      : readOnly && index < 2
        ? 'todo'
        : index === current
          ? 'current'
          : 'todo'
  // Quick mode, when offered, holds the only primary button.
  const variantOf = (index: number) =>
    index === current && !plan.shown ? 'default' : 'outline'
  const [upstreamCount = 0, profileCount = 0, keyCount = 0] = counts
  const keyReady = loaded && done[0] && done[1]
  // Upstreams imported from the server's VLM settings carry a `vlm-` id.
  const importedCount =
    upstreams.data?.filter((item) => item.id.startsWith('vlm-')).length ?? 0
  const blockedUpstreams = (upstreams.data ?? []).filter(
    (item) => item.coding_plan && !item.allow_coding_plan,
  )

  const viewButton = (
    to:
      | '/admin/gateway/upstreams'
      | '/admin/gateway/profiles'
      | '/admin/gateway/keys',
  ) => (
    <Button
      size="sm"
      variant="ghost"
      nativeButton={false}
      render={<Link to={to} />}
    >
      {t('overview.setup.view')}
      <ArrowRightIcon />
    </Button>
  )

  return (
    <Card className="border-primary/25 bg-primary/[0.025] ring-primary/10">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <span className="flex size-7 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <ListChecksIcon className="size-4" />
          </span>
          {t('overview.setup.title')}
        </CardTitle>
        {loaded ? (
          <CardAction>
            <span className="rounded-full border bg-background/70 px-2.5 py-1 text-xs text-muted-foreground tabular-nums">
              {t('overview.setup.progress', {
                done: done.filter(Boolean).length,
                total: done.length,
              })}
            </span>
          </CardAction>
        ) : null}
      </CardHeader>
      <CardContent className="grid gap-4">
        {plan.shown ? (
          <>
            <OverviewQuickStart
              plan={plan}
              onIssued={(key, profileName, list) =>
                showSecret({ issued: key, profileName, upstreams: list })
              }
              onPickUser={openIssueDialog}
            />
            <div className="flex items-center gap-3 text-xs text-muted-foreground">
              <span className="h-px flex-1 bg-border" />
              {t('overview.quick.divider')}
              <span className="h-px flex-1 bg-border" />
            </div>
          </>
        ) : null}
        {vlmImport.result && needsReview(vlmImport.result) ? (
          <ImportResultNotice
            result={vlmImport.result}
            onDismiss={vlmImport.dismiss}
          />
        ) : null}
        <ol className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <SetupStep
            number={1}
            state={stateOf(0)}
            title={t('overview.setup.upstream.title')}
            description={
              <>
                <p>
                  {done[0]
                    ? t(
                        readOnly
                          ? 'overview.setup.providedUpstreams'
                          : !importedCount
                            ? 'overview.setup.upstream.done'
                            : importedCount < upstreamCount
                              ? 'overview.setup.someImportedUpstreams'
                              : 'overview.setup.importedUpstreams',
                        { count: upstreamCount, imported: importedCount },
                      )
                    : t(
                        readOnly
                          ? 'overview.setup.waitUpstreams'
                          : 'overview.setup.upstream.description',
                      )}
                </p>
                {readOnly
                  ? null
                  : blockedUpstreams.map((item) => (
                      <p
                        key={item.id}
                        className="mt-1 flex items-start gap-1.5 text-xs text-amber-800 dark:text-amber-200"
                      >
                        <TriangleAlertIcon className="mt-0.5 size-3.5 shrink-0" />
                        <span>
                          {t('overview.setup.codingPlan', { name: item.name })}{' '}
                          <Link
                            to="/admin/gateway/upstreams/$upstreamId"
                            params={{ upstreamId: item.id }}
                            className="font-medium underline underline-offset-4"
                          >
                            {t('overview.setup.confirm')}
                          </Link>
                        </span>
                      </p>
                    ))}
              </>
            }
          >
            {done[0] ? (
              viewButton('/admin/gateway/upstreams')
            ) : readOnly ? (
              <ManagementUnlockButton />
            ) : (
              <>
                {access?.can_import_vlm ? (
                  <>
                    <Button
                      type="button"
                      size="sm"
                      variant={variantOf(0)}
                      disabled={importing || !upstreams.data}
                      title={t('upstreams.import.hint')}
                      onClick={() => vlmImport.importer.mutate()}
                    >
                      {importing ? (
                        <LoaderCircleIcon className="animate-spin" />
                      ) : (
                        <DownloadIcon />
                      )}
                      {t(
                        importing
                          ? 'upstreams.import.running'
                          : 'upstreams.import.action',
                      )}
                    </Button>
                    <StepLink
                      nativeButton={false}
                      render={
                        <Link
                          to="/admin/gateway/upstreams/$upstreamId"
                          params={{ upstreamId: NEW_ID }}
                        />
                      }
                    >
                      {t('overview.setup.upstream.manual')}
                    </StepLink>
                  </>
                ) : (
                  <Button
                    size="sm"
                    variant={variantOf(0)}
                    nativeButton={false}
                    render={
                      <Link
                        to="/admin/gateway/upstreams/$upstreamId"
                        params={{ upstreamId: NEW_ID }}
                      />
                    }
                  >
                    {t('overview.setup.upstream.action')}
                  </Button>
                )}
              </>
            )}
          </SetupStep>
          <SetupStep
            number={2}
            state={stateOf(1)}
            title={t('overview.setup.profile.title')}
            description={
              done[1]
                ? t(
                    readOnly
                      ? 'overview.setup.providedProfiles'
                      : 'overview.setup.profile.done',
                    { count: profileCount },
                  )
                : t(
                    readOnly
                      ? 'overview.setup.waitProfiles'
                      : 'overview.setup.profile.description',
                  )
            }
          >
            {done[1] ? (
              viewButton('/admin/gateway/profiles')
            ) : readOnly ? (
              <ManagementUnlockButton />
            ) : (
              <>
                <RecommendedProfileButton
                  label={t('overview.setup.profile.action')}
                  variant={variantOf(1)}
                  disabled={!profiles.data}
                />
                <StepLink
                  nativeButton={false}
                  render={
                    <Link
                      to="/admin/gateway/profiles/$profileId"
                      params={{ profileId: NEW_ID }}
                    />
                  }
                >
                  {t('profiles.actions.customize')}
                </StepLink>
              </>
            )}
          </SetupStep>
          <SetupStep
            number={3}
            state={stateOf(2)}
            title={t('overview.setup.key.title')}
            description={t(
              done[2]
                ? 'overview.setup.key.done'
                : !keyReady
                  ? readOnly
                    ? 'overview.setup.key.waiting'
                    : 'overview.setup.key.blocked'
                  : 'overview.setup.key.description',
              { count: keyCount },
            )}
          >
            {done[2] ? (
              viewButton('/admin/gateway/keys')
            ) : (
              <>
                <Button
                  type="button"
                  size="sm"
                  variant={variantOf(2)}
                  disabled={!keyReady || issue.isPending}
                  onClick={issueNow}
                >
                  {issue.isPending ? (
                    <LoaderCircleIcon className="animate-spin" />
                  ) : (
                    <KeyRoundIcon />
                  )}
                  {t('overview.setup.key.action')}
                </Button>
                <StepLink
                  type="button"
                  disabled={!keyReady}
                  onClick={() => openIssueDialog(keyRequest)}
                >
                  {t('profiles.actions.customize')}
                </StepLink>
              </>
            )}
          </SetupStep>
          <SetupStep
            number={4}
            state={stateOf(3)}
            title={t('overview.setup.connect.title')}
            description={t('overview.setup.connect.description')}
          >
            <Button
              size="sm"
              variant={variantOf(3)}
              nativeButton={false}
              render={<Link to="/admin/gateway/connect" />}
            >
              {t('overview.setup.connect.action')}
            </Button>
            <StepLink
              type="button"
              disabled={!baseUrl}
              onClick={() => void copyAddress()}
            >
              {t('address.copy')}
            </StepLink>
          </SetupStep>
        </ol>
      </CardContent>
      <KeysIssueDialog
        key={issueSession}
        open={issueDefaults !== null}
        onOpenChange={(open) => {
          if (!open) setIssueDefaults(null)
        }}
        profiles={profiles.data ?? []}
        upstreams={upstreams.data ?? []}
        defaults={issueDefaults ?? undefined}
        onIssued={(key) => {
          setIssueDefaults(null)
          showSecret({
            issued: key,
            profileName: profiles.data?.find(
              (profile) => profile.id === key.policy_id,
            )?.name,
            upstreams: upstreams.data ?? [],
          })
        }}
      />
      <KeysSecretDialog
        open={secretOpen}
        issued={issued?.issued ?? null}
        baseUrl={baseUrl ?? ''}
        profileName={issued?.profileName}
        upstreams={issued?.upstreams ?? []}
        onDone={() => setSecretOpen(false)}
        onClosed={() => setIssued(null)}
      />
    </Card>
  )
}
