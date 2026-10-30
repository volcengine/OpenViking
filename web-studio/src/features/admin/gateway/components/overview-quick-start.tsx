import { useMutation } from '@tanstack/react-query'
import { LoaderCircleIcon, ZapIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '#/shared/ui/button'

import { ManagementUnlockButton } from '#/features/admin/gateway/components/access-notices'
import { DocsLink } from '#/features/admin/gateway/components/docs-link'
import { issueProblem } from '#/features/admin/gateway/components/keys-issue-dialog'
import { Notice } from '#/features/admin/gateway/components/notice'
import { ImportResultNotice } from '#/features/admin/gateway/components/upstreams-page'
import type {
  IssuedKey,
  KeyRequest,
  Profile,
  Upstream,
  VlmImportResult,
} from '#/features/admin/gateway/lib/api'
import {
  importVlm,
  issueKey,
  listProfiles,
  listUpstreams,
  saveProfile,
  toGatewayError,
} from '#/features/admin/gateway/lib/api'
import { protocolLabel } from '#/features/admin/gateway/lib/localize'
import { PROFILE_DEFAULTS } from '#/features/admin/gateway/lib/profile-schema'
import type { QuickPlan } from '#/features/admin/gateway/lib/quick-start'
import {
  QUICK_PROFILE_ID,
  isQuickUsable,
  quickProfile,
} from '#/features/admin/gateway/lib/quick-start'
import { servedModels } from '#/features/admin/gateway/lib/upstream-schema'
import { useGateway } from '#/features/admin/gateway/lib/use-gateway'

const MODEL_PREVIEW = 3

type Outcome =
  | {
      kind: 'issued'
      issued: IssuedKey
      profile: Profile
      upstreams: Upstream[]
    }
  | { kind: 'pick'; request: KeyRequest }
  | { kind: 'noUsable'; result: VlmImportResult }

type OverviewQuickStartProps = {
  plan: QuickPlan
  onIssued: (
    issued: IssuedKey,
    profileName: string,
    upstreams: Upstream[],
  ) => void
  /** Root picks the key's user in the issue dialog, prefilled with this request. */
  onPickUser: (request: KeyRequest) => void
}

/**
 * One click from an empty gateway to a working key: imports OpenViking's
 * model settings when no upstream is usable, creates the recommended profile
 * when there is none, then issues a key for the current identity.
 */
export function OverviewQuickStart({
  plan,
  onIssued,
  onPickUser,
}: OverviewQuickStartProps) {
  const { t, i18n } = useTranslation('gateway')
  const { connection, access, invalidate } = useGateway()
  const list = new Intl.ListFormat(i18n.resolvedLanguage)

  const run = useMutation({
    mutationFn: async (): Promise<Outcome> => {
      let upstreams = await listUpstreams(connection)
      let usable = upstreams.filter(isQuickUsable)
      if (!usable.length) {
        const result = await importVlm(connection)
        upstreams = await listUpstreams(connection)
        usable = upstreams.filter(isQuickUsable)
        if (!usable.length) return { kind: 'noUsable', result }
      }
      const profile =
        quickProfile(await listProfiles(connection)) ??
        (await saveProfile(connection, QUICK_PROFILE_ID, {
          ...PROFILE_DEFAULTS,
          name: t('profiles.defaultName'),
        }))
      const request: KeyRequest = {
        name: t('overview.quick.keyName'),
        policy_id: profile.id,
        upstream_ids: usable.map((upstream) => upstream.id),
        models: [],
      }
      if (plan.pickUser) return { kind: 'pick', request }
      const issued = await issueKey(connection, request)
      return { kind: 'issued', issued, profile, upstreams }
    },
    onSuccess: async (outcome) => {
      if (outcome.kind === 'issued') {
        // The card goes away with the refreshed key list; the secret stays.
        onIssued(outcome.issued, outcome.profile.name, outcome.upstreams)
        await invalidate('upstreams', 'profiles', 'keys', 'overview', 'tools')
      } else if (outcome.kind === 'pick') {
        await invalidate('upstreams', 'profiles')
        onPickUser(outcome.request)
      } else {
        await invalidate('upstreams')
      }
    },
    onError: (error) =>
      invalidate(
        'upstreams',
        'profiles',
        // A stale /access offered the import; the refreshed one hides it.
        ...(toGatewayError(error).reason === 'vlm_import_forbidden'
          ? (['access'] as const)
          : []),
      ),
  })
  const noUsable = run.data?.kind === 'noUsable' ? run.data.result : undefined
  const failed = run.isError || Boolean(noUsable)

  const models = [...new Set(plan.upstreams.flatMap(servedModels))]
  const upstreamLine = plan.upstreams.length
    ? t('overview.quick.upstreamUse', {
        names: list.format(plan.upstreams.map((upstream) => upstream.name)),
        models:
          list.format(models.slice(0, MODEL_PREVIEW)) +
          (models.length > MODEL_PREVIEW ? '…' : ''),
        protocol: list.format([
          ...new Set(
            plan.upstreams.map((upstream) =>
              protocolLabel(t, upstream.protocol),
            ),
          ),
        ]),
      })
    : t('overview.quick.upstreamImport')
  const lines = [
    upstreamLine,
    plan.profile
      ? t('overview.quick.profileUse', { name: plan.profile.name })
      : t('overview.quick.profileCreate', { name: t('profiles.defaultName') }),
    plan.pickUser
      ? t('overview.quick.keyPick')
      : t('overview.quick.keySelf', {
          user: access?.user_id ?? connection.userId,
        }),
  ]

  return (
    // The one-click button stands in for the box's heading; the region keeps
    // "Quick mode" as its accessible name.
    <section
      aria-label={t('overview.quick.title')}
      className="grid gap-3 rounded-lg border border-primary/30 bg-background/70 p-4"
    >
      <div className="grid justify-items-start gap-2">
        {plan.runnable ? (
          <Button
            type="button"
            size="sm"
            disabled={run.isPending}
            onClick={() => run.mutate()}
          >
            {run.isPending ? (
              <LoaderCircleIcon className="animate-spin" />
            ) : (
              <ZapIcon />
            )}
            {t(
              run.isPending
                ? 'overview.quick.running'
                : failed
                  ? 'overview.quick.retry'
                  : 'overview.quick.action',
            )}
          </Button>
        ) : (
          <p className="flex items-center gap-2 font-medium">
            <ZapIcon className="size-4 text-primary" />
            {t('overview.quick.title')}
          </p>
        )}
        <p className="text-sm text-muted-foreground">
          {t('overview.quick.description')}{' '}
          <DocsLink section="quickMode" variant="text" />
        </p>
      </div>
      <ul className="grid list-inside list-disc gap-0.5 text-sm text-muted-foreground">
        {lines.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      {run.isError ? (
        <Notice tone="danger">{issueProblem(t, run.error).message}</Notice>
      ) : null}
      {noUsable ? (
        <>
          <Notice tone="warning">{t('overview.quick.noUsable')}</Notice>
          <ImportResultNotice result={noUsable} onDismiss={run.reset} />
        </>
      ) : null}
      {plan.runnable ? null : (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm text-muted-foreground">
            {t('overview.quick.locked')}
          </p>
          <ManagementUnlockButton />
        </div>
      )}
    </section>
  )
}
