import { ExperienceDashboard } from '@openviking/react/components/experience-dashboard'
import '@openviking/react/components/experience-dashboard/style.css'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { Button } from '#/shared/ui/button'
import { Input } from '#/shared/ui/input'
import { ExperienceSetupGuide } from './components/experience-setup-guide'
import { fetchExperiences, searchExperiences } from './lib/api'
import { buildExperiencesUri } from './lib/experience'

const PAGE_SIZE = 20

export function AgentExperienceRoute() {
  const { connection, identityScopeKey } = useAppConnection()
  const navigate = useNavigate()
  const uri = buildExperiencesUri(connection.userId)

  return (
    <AgentExperienceList
      key={identityScopeKey}
      directoryUri={uri}
      onOpenFile={(experienceUri) => {
        void navigate({
          to: '/agent-experience/$experienceUri',
          params: { experienceUri },
        })
      }}
      standalone
    />
  )
}

export function AgentExperienceList({
  directoryUri,
  onOpenFile,
  standalone = false,
}: {
  directoryUri: string
  onOpenFile: (uri: string) => void
  standalone?: boolean
}) {
  const { t, i18n } = useTranslation('agentExperiencePage')
  const { identityScopeKey } = useAppConnection()
  const [keyword, setKeyword] = useState('')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(0)
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSearch(keyword.trim())
      setPage(0)
    }, 300)
    return () => window.clearTimeout(timer)
  }, [keyword])

  const query = useQuery({
    queryKey: [
      'agent-experience-list',
      identityScopeKey,
      directoryUri,
      search,
      page,
    ],
    queryFn: ({ signal }) =>
      search
        ? searchExperiences({
            experiencesUri: directoryUri,
            keyword: search,
            signal,
          })
        : fetchExperiences({
            experiencesUri: directoryUri,
            page: page + 1,
            pageSize: PAGE_SIZE,
            signal,
          }),
    staleTime: 30_000,
  })

  return (
    <div className="flex min-w-0 flex-col gap-4">
      {standalone ? (
        <>
          <h1 className="text-2xl font-semibold tracking-tight">
            {t('title')}
          </h1>
          <ExperienceSetupGuide />
        </>
      ) : null}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Input
          aria-label={t('searchPlaceholder')}
          className="max-w-sm"
          placeholder={t('searchPlaceholder')}
          type="search"
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
        />
        <Button
          variant="outline"
          size="sm"
          disabled={query.isFetching}
          onClick={() => void query.refetch()}
        >
          {t('refresh')}
        </Button>
      </div>
      <ExperienceDashboard
        uri={directoryUri}
        kind="directory"
        locale={i18n.language.startsWith('zh') ? 'zh-CN' : 'en-US'}
        data={{ experiences: query.data?.items }}
        states={{
          list: {
            loading: query.isPending,
            error: query.error,
            onRetry: () => void query.refetch(),
          },
        }}
        page={page}
        pageSize={PAGE_SIZE}
        startIndex={page * PAGE_SIZE}
        hasMore={query.data?.hasMore}
        onPageChange={setPage}
        onOpenUri={onOpenFile}
        labels={
          search
            ? {
                allExperiences: t('searchPlaceholder'),
                empty: t('searchNoResults'),
              }
            : undefined
        }
      />
    </div>
  )
}
