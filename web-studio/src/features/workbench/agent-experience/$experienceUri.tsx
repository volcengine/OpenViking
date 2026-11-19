import { ExperienceDashboard } from '@openviking/react/components/experience-dashboard'
import type { ExperienceDashboardTab } from '@openviking/react/components/experience-dashboard'
import '@openviking/react/components/experience-dashboard/style.css'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { getRouteApi, Link, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { LazyFilePreview } from '#/features/workbench/resources/components/lazy-file-preview'
import { fetchFsStat } from '#/features/workbench/resources/lib/api'
import { fileNameFromUri } from '#/shared/lib/viking-uri'
import { Button } from '#/shared/ui/button'
import {
  fetchOutcomeDistribution,
  fetchSourceTrajectories,
  fetchTrajectories,
} from './lib/api'
import { resolveTimeRange } from './lib/experience'

const routeApi = getRouteApi('/agent-experience/$experienceUri')

export function ExperienceDetailRoute() {
  const { experienceUri } = routeApi.useParams()
  const { identityScopeKey } = useAppConnection()
  return (
    <ExperienceDetail
      key={`${identityScopeKey}:${experienceUri}`}
      experienceUri={experienceUri}
    />
  )
}

function ExperienceDetail({ experienceUri }: { experienceUri: string }) {
  const { t, i18n } = useTranslation('agentExperiencePage')
  const { identityScopeKey } = useAppConnection()
  const navigate = useNavigate()
  const [tab, setTab] = useState<ExperienceDashboardTab>('impact')
  const [days, setDays] = useState(0)
  const timeRange = resolveTimeRange(days)
  const openResource = (uri: string) => {
    void navigate({
      to: '/filesystem',
      search: uri.endsWith('/') ? { uri } : { file: uri },
    })
  }
  const file = useQuery({
    queryKey: ['agent-experience-file', identityScopeKey, experienceUri],
    queryFn: () => fetchFsStat(experienceUri, { throwOnError: true }),
  })
  const outcomes = useQuery({
    enabled: tab === 'impact',
    queryKey: [
      'agent-experience-outcomes',
      identityScopeKey,
      experienceUri,
      timeRange,
    ],
    queryFn: ({ signal }) =>
      fetchOutcomeDistribution({ experienceUri, timeRange, signal }),
    retry: false,
  })
  const trajectories = useInfiniteQuery({
    enabled: tab === 'impact',
    queryKey: [
      'agent-experience-trajectories',
      identityScopeKey,
      experienceUri,
      timeRange,
    ],
    initialPageParam: 0,
    queryFn: ({ pageParam, signal }) =>
      fetchTrajectories({
        experienceUri,
        offset: pageParam,
        timeRange,
        signal,
      }),
    getNextPageParam: (last) =>
      last.hasMore && last.items.length > 0
        ? last.offset + last.items.length
        : undefined,
    retry: false,
  })
  const sources = useQuery({
    enabled: tab === 'source',
    queryKey: ['agent-experience-sources', identityScopeKey, experienceUri],
    queryFn: ({ signal }) => fetchSourceTrajectories(experienceUri, signal),
    retry: false,
  })
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <header className="flex flex-wrap items-center gap-3">
        <Button
          render={<Link to="/agent-experience" />}
          nativeButton={false}
          variant="ghost"
          size="sm"
        >
          {t('detail.back')}
        </Button>
        <h1 className="min-w-0 truncate text-xl font-semibold">
          {fileNameFromUri(experienceUri)}
        </h1>
      </header>
      <div className="grid min-w-0 items-start gap-4 xl:grid-cols-2">
        <div className="flex h-[min(65vh,640px)] min-w-0 flex-col overflow-hidden rounded-lg border">
          {file.isError ? (
            <div className="grid gap-2 p-4" role="alert">
              {file.error.message}
              <Button
                variant="outline"
                size="sm"
                className="w-fit"
                onClick={() => void file.refetch()}
              >
                {t('refresh')}
              </Button>
            </div>
          ) : file.data ? (
            <LazyFilePreview
              file={file.data}
              readOnly
              showCloseButton={false}
              onClose={() => {}}
              onNavigate={openResource}
            />
          ) : (
            <p role="status" className="p-4">
              {t('detail.contentLoading')}
            </p>
          )}
        </div>
        <ExperienceDashboard
          uri={experienceUri}
          kind="file"
          locale={i18n.language.startsWith('zh') ? 'zh-CN' : 'en-US'}
          tab={tab}
          onTabChange={setTab}
          days={days}
          onDaysChange={setDays}
          data={{
            outcomes: outcomes.data?.distribution,
            trajectories: trajectories.data?.pages.flatMap(
              (page) => page.items,
            ),
            trajectoryTotal: trajectories.data?.pages[0]?.total,
            sources: sources.data?.map(({ uri, reason }) => ({
              uri,
              name: fileNameFromUri(uri),
              description: reason,
            })),
          }}
          states={{
            outcomes: {
              loading: outcomes.isPending,
              error: outcomes.error,
              onRetry: () => void outcomes.refetch(),
            },
            trajectories: {
              loading: trajectories.isPending,
              error: trajectories.isFetchNextPageError
                ? null
                : trajectories.error,
              onRetry: () => void trajectories.refetch(),
            },
            sources: {
              loading: sources.isPending,
              error: sources.error,
              onRetry: () => void sources.refetch(),
            },
          }}
          hasMore={trajectories.hasNextPage}
          loadingMore={trajectories.isFetchingNextPage}
          onLoadMore={() => void trajectories.fetchNextPage()}
          onOpenUri={openResource}
        />
      </div>
    </div>
  )
}
