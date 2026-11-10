import { LightbulbIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { DocsLink } from '#/features/admin/gateway/components/docs-link'
import { Notice } from '#/features/admin/gateway/components/notice'
import type {
  CacheAlerts,
  CacheMiss,
  Upstream,
} from '#/features/admin/gateway/lib/api'
import {
  formatCompact,
  formatDateTime,
  formatRelativeTime,
  shortenId,
} from '#/features/admin/gateway/lib/format'
import { useUpstreams } from '#/features/admin/gateway/lib/use-gateway'

/** Upstream name, or its shortened id once deleted or not yet loaded. */
function upstreamName(id: string, upstreams?: Upstream[]): string {
  return (
    upstreams?.find((upstream) => upstream.id === id)?.name ?? shortenId(id)
  )
}

/** One conversation: model · upstream · streak · last call. */
function CacheMissRow({
  miss,
  upstreams,
}: {
  miss: CacheMiss
  upstreams?: Upstream[]
}) {
  const { t, i18n } = useTranslation('gateway')
  const locale = i18n.resolvedLanguage
  return (
    <li className="flex min-w-0 flex-wrap gap-x-1.5 text-xs leading-5 text-foreground/80">
      {miss.model ? (
        <>
          <span className="truncate font-mono">{miss.model}</span>
          <span aria-hidden>·</span>
        </>
      ) : null}
      <span title={miss.upstream_id}>
        {upstreamName(miss.upstream_id, upstreams)}
      </span>
      <span aria-hidden>·</span>
      <span className="tabular-nums">
        {t('overview.cacheMiss.streak', { count: miss.streak })}
      </span>
      <span aria-hidden>·</span>
      <span title={formatDateTime(miss.last_at, locale, true)}>
        {formatRelativeTime(miss.last_at, locale)}
      </span>
    </li>
  )
}

/**
 * Long conversations that keep missing the prompt cache, and upstreams that
 * never report a hit; upstream names load only while there is one to show.
 */
export function CacheNotices({ alerts }: { alerts: CacheAlerts }) {
  const { t, i18n } = useTranslation('gateway')
  const locale = i18n.resolvedLanguage
  const upstreams = useUpstreams().data
  const { misses, unreported } = alerts
  const docs = (
    <DocsLink section="cacheHitRate">{t('docs.troubleshoot')}</DocsLink>
  )
  return (
    <div className="grid gap-3">
      {misses ? (
        <Notice
          tone="warning"
          title={t('overview.cacheMiss.title', { count: misses.conversations })}
          action={docs}
        >
          <p className="text-foreground/80">
            {t('overview.cacheMiss.description', {
              count: misses.conversations,
              streak: misses.streak,
              // Rounded down, so "at least" holds for every conversation.
              tokens: formatCompact(
                Math.floor(misses.input_tokens / 1000) * 1000,
                locale,
              ),
            })}
          </p>
          <p className="flex gap-1.5 text-xs leading-5 text-foreground/80">
            <LightbulbIcon className="mt-0.5 size-3.5 shrink-0" />
            <span>
              <span className="sr-only">
                {t('overview.degraded.suggestion')}
              </span>
              {t(
                misses.no_writes
                  ? 'overview.cacheMiss.noWrites'
                  : 'overview.cacheMiss.action',
              )}
            </span>
          </p>
          <ul className="mt-1 grid gap-0.5">
            {misses.recent.map((miss, index) => (
              <CacheMissRow
                key={`${miss.upstream_id}-${miss.last_at}-${index}`}
                miss={miss}
                upstreams={upstreams}
              />
            ))}
          </ul>
        </Notice>
      ) : null}
      {unreported.length ? (
        <Notice
          tone="info"
          title={t('overview.cacheUnreported.title', {
            count: unreported.length,
            upstreams: unreported
              .map((id) => upstreamName(id, upstreams))
              .join(t('overview.cacheUnreported.separator')),
          })}
          action={docs}
        >
          <p className="text-foreground/80">
            {t('overview.cacheUnreported.description', {
              count: unreported.length,
            })}
          </p>
        </Notice>
      ) : null}
    </div>
  )
}
