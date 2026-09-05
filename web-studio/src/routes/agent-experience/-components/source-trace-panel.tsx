import { useQuery } from '@tanstack/react-query'
import { LinkIcon, LoaderCircleIcon, RouteIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '#/components/ui/button'
import { useAppConnection } from '#/hooks/use-app-connection'

import { fetchSourceTrajectories } from '../-lib/api'
import type { TrajectoryItem } from '../-lib/types'

/**
 * Source lineage: `derived_from` links stored in the Experience memory attrs.
 */
export function SourceTracePanel({
  experienceUri,
  onSelect,
}: {
  experienceUri: string
  onSelect: (trajectory: TrajectoryItem) => void
}) {
  const { t } = useTranslation('agentExperiencePage')
  const { identityScopeKey } = useAppConnection()
  const sourceQuery = useQuery({
    queryFn: ({ signal }) => fetchSourceTrajectories(experienceUri, signal),
    queryKey: ['agent-experience-sources', identityScopeKey, experienceUri],
    staleTime: 60_000,
  })

  const links = sourceQuery.data ?? []

  return (
    <section className="grid gap-3">
      <p className="text-sm text-muted-foreground">
        {t('detail.sourceDescription')}
      </p>
      {sourceQuery.isLoading ? (
        <div className="flex min-h-24 items-center gap-2 text-sm text-muted-foreground">
          <LoaderCircleIcon className="size-4 animate-spin" />
          {t('detail.sourceLoading')}
        </div>
      ) : sourceQuery.isError ? (
        <div className="grid min-h-24 place-items-center gap-2 text-center">
          <p className="text-sm text-muted-foreground">
            {t('detail.sourceLoadFailed')}
          </p>
          <Button
            type="button"
            size="xs"
            variant="outline"
            onClick={() => void sourceQuery.refetch()}
          >
            {t('refresh')}
          </Button>
        </div>
      ) : links.length === 0 ? (
        <div className="grid min-h-24 place-items-center px-4 py-4 text-center">
          <p className="max-w-md text-sm text-muted-foreground">
            {t('detail.sourceEmpty')}
          </p>
        </div>
      ) : (
        <ul className="grid gap-2">
          {links.map((link) => {
            const name = link.uri.split('/').pop() ?? link.uri
            return (
              <li key={`${link.uri}:${link.reason ?? ''}`}>
                <button
                  type="button"
                  aria-label={t('detail.trajectoryView', { name })}
                  className="grid w-full gap-1 rounded-lg border px-3 py-2.5 text-left transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
                  onClick={() =>
                    onSelect({
                      description: link.reason,
                      name,
                      uri: link.uri,
                    })
                  }
                >
                  <span className="flex min-w-0 items-center gap-2">
                    <RouteIcon className="size-3.5 shrink-0 text-muted-foreground" />
                    <span className="truncate text-sm font-medium" title={name}>
                      {name}
                    </span>
                  </span>
                  <span className="flex min-w-0 items-center gap-1.5 pl-5.5">
                    <LinkIcon className="size-3 shrink-0 text-muted-foreground/70" />
                    <code
                      className="min-w-0 truncate text-xs text-muted-foreground"
                      title={link.uri}
                    >
                      {link.uri}
                    </code>
                  </span>
                  {link.reason ? (
                    <span
                      className="truncate pl-5.5 text-xs text-muted-foreground/80"
                      title={link.reason}
                    >
                      {link.reason}
                    </span>
                  ) : null}
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
