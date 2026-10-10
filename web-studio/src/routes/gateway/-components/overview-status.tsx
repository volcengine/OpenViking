import { Link } from '@tanstack/react-router'
import { ArrowRightIcon, CircleCheckIcon, LightbulbIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '#/components/ui/button'
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '#/components/ui/card'

import type { OpenVikingHealth, Overview } from '../-lib/api'
import { formatNumber } from '../-lib/format'
import { degradationInfo, healthReasonLabel } from '../-lib/localize'
import { EmptyState } from './empty-state'
import { Notice } from './notice'
import { EmptyValue, HealthBadge, ToneBadge } from './status-badges'

/** Link to the request log filtered to problems. */
function IssuesLink({ className }: { className?: string }) {
  const { t } = useTranslation('gateway')
  return (
    <Button
      size="sm"
      variant="ghost"
      className={className}
      nativeButton={false}
      render={<Link to="/gateway/requests" search={{ filter: 'issues' }} />}
    >
      {t('overview.showIssues')}
      <ArrowRightIcon />
    </Button>
  )
}

/** Conversations whose saving to OpenViking is retrying or paused. */
function SavingStatus({ issues }: { issues: Overview['capture_issues'] }) {
  const { t } = useTranslation('gateway')
  const { retrying, paused } = issues
  return (
    <div className="grid gap-2 border-t pt-4">
      <p className="text-sm font-medium">{t('overview.saving.title')}</p>
      {retrying > 0 || paused > 0 ? (
        <>
          <Notice tone="warning">
            {retrying > 0 ? (
              <p>{t('overview.saving.retrying', { count: retrying })}</p>
            ) : null}
            {paused > 0 ? (
              <p>{t('overview.saving.paused', { count: paused })}</p>
            ) : null}
          </Notice>
          <div>
            {/* Cancels the button padding so the label lines up with the text. */}
            <IssuesLink className="-ml-2.5" />
          </div>
        </>
      ) : (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <CircleCheckIcon className="size-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
          {t('overview.saving.ok')}
        </p>
      )}
    </div>
  )
}

/** OpenViking health as last checked by the gateway, plus saving problems. */
export function OpenVikingCard({
  health,
  captureIssues,
}: {
  health: OpenVikingHealth
  captureIssues: Overview['capture_issues']
}) {
  const { t } = useTranslation('gateway')
  const { status, version, auth_mode: authMode, reason } = health
  return (
    <Card className="gap-4">
      <CardHeader>
        <CardTitle>{t('overview.openviking.title')}</CardTitle>
        <CardDescription>
          {t('overview.openviking.description')}
        </CardDescription>
        <CardAction>
          <HealthBadge status={status} />
        </CardAction>
      </CardHeader>
      <CardContent className="grid gap-4">
        {status === 'ok' ? (
          <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-6 gap-y-2 text-sm">
            <dt className="text-muted-foreground">
              {t('overview.openviking.version')}
            </dt>
            <dd className="truncate font-mono text-xs leading-5">
              {version || <EmptyValue />}
            </dd>
            <dt className="text-muted-foreground">
              {t('overview.openviking.authMode')}
            </dt>
            <dd>
              {authMode ? (
                t(`overview.openviking.authModes.${authMode}`, {
                  defaultValue: authMode,
                })
              ) : (
                <EmptyValue />
              )}
            </dd>
          </dl>
        ) : null}
        {status === 'degraded' ? (
          <Notice
            tone="danger"
            title={reason ? healthReasonLabel(t, reason) : undefined}
          >
            <p className="text-foreground/80">
              {t('overview.openviking.unreachable')}
            </p>
          </Notice>
        ) : null}
        {status === 'starting' ? (
          <p className="text-sm leading-6 text-muted-foreground">
            {t('overview.openviking.starting')}
          </p>
        ) : null}
        <SavingStatus issues={captureIssues} />
      </CardContent>
    </Card>
  )
}

/** Degradation reasons by count, each with what happened and what to do. */
export function DegradedRequestsCard({
  degradations,
}: {
  degradations: Overview['degradations']
}) {
  const { t, i18n } = useTranslation('gateway')
  const entries = Object.entries(degradations)
    .filter(([, count]) => count > 0)
    .sort(([, a], [, b]) => b - a)
  return (
    <Card className="gap-4">
      <CardHeader>
        <CardTitle>{t('overview.degraded.title')}</CardTitle>
        <CardDescription>{t('overview.degraded.description')}</CardDescription>
        {entries.length > 0 ? (
          <CardAction>
            <IssuesLink />
          </CardAction>
        ) : null}
      </CardHeader>
      <CardContent>
        {entries.length > 0 ? (
          <ul className="divide-y">
            {entries.map(([reason, count]) => {
              const info = degradationInfo(t, reason)
              return (
                <li
                  key={reason}
                  className="flex items-start justify-between gap-4 py-3 first:pt-0 last:pb-0"
                >
                  <div className="grid min-w-0 gap-1">
                    <p className="font-medium">{info.label}</p>
                    {info.explanation ? (
                      <p className="text-sm leading-6 text-muted-foreground">
                        {info.explanation}
                      </p>
                    ) : null}
                    {info.action ? (
                      <p className="flex gap-1.5 text-xs leading-5 text-muted-foreground">
                        <LightbulbIcon className="mt-0.5 size-3.5 shrink-0" />
                        <span>
                          <span className="sr-only">
                            {t('overview.degraded.suggestion')}
                          </span>
                          {info.action}
                        </span>
                      </p>
                    ) : null}
                  </div>
                  <ToneBadge tone="warning" className="shrink-0 tabular-nums">
                    {formatNumber(count, i18n.resolvedLanguage)}
                  </ToneBadge>
                </li>
              )
            })}
          </ul>
        ) : (
          <EmptyState
            className="min-h-40 py-6"
            icon={<CircleCheckIcon />}
            title={t('overview.degraded.empty.title')}
            description={t('overview.degraded.empty.description')}
          />
        )}
      </CardContent>
    </Card>
  )
}
