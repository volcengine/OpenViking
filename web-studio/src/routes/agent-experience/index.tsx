import * as React from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, createFileRoute } from '@tanstack/react-router'
import {
  BrainCircuitIcon,
  FileTextIcon,
  ArrowUpRightIcon,
  LoaderCircleIcon,
  MessageSquareTextIcon,
  RefreshCwIcon,
  SearchIcon,
  XIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Badge } from '#/components/ui/badge'
import { Button } from '#/components/ui/button'
import { Card } from '#/components/ui/card'
import { Input } from '#/components/ui/input'
import {
  Pagination,
  PaginationContent,
  PaginationItem,
  PaginationNext,
  PaginationPrevious,
} from '#/components/ui/pagination'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '#/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '#/components/ui/table'
import { useAppConnection } from '#/hooks/use-app-connection'
import { isOvClientError } from '#/lib/ov-client'
import { cn } from '#/lib/utils'

import { EvolutionSettingsPopover } from './-components/evolution-settings-popover'
import { ExperiencePreviewSheet } from './-components/experience-preview-sheet'
import { fetchExperiences } from './-lib/api'
import {
  buildExperiencesUri,
  formatTimestamp,
  isExperienceUpdatedSinceLastSeen,
  markExperiencesSeen,
} from './-lib/experience'
import type { ExperienceFileItem } from './-lib/types'

export const Route = createFileRoute('/agent-experience/')({
  component: AgentExperienceRoute,
})

function getErrorMessage(error: unknown): string {
  if (isOvClientError(error) || error instanceof Error) {
    return error.message
  }
  return String(error)
}

function HighlightedText({ keyword, text }: { keyword: string; text: string }) {
  const normalizedKeyword = keyword.trim().toLocaleLowerCase()
  if (!normalizedKeyword) return <>{text}</>

  const normalizedText = text.toLocaleLowerCase()
  const fragments: React.ReactNode[] = []
  let cursor = 0
  let matchIndex = normalizedText.indexOf(normalizedKeyword)

  while (matchIndex !== -1) {
    if (matchIndex > cursor) {
      fragments.push(text.slice(cursor, matchIndex))
    }
    const matchEnd = matchIndex + normalizedKeyword.length
    fragments.push(
      <mark
        key={`${matchIndex}-${matchEnd}`}
        className="rounded-xs bg-primary/15 px-0.5 text-inherit"
      >
        {text.slice(matchIndex, matchEnd)}
      </mark>,
    )
    cursor = matchEnd
    matchIndex = normalizedText.indexOf(normalizedKeyword, cursor)
  }
  if (cursor < text.length) {
    fragments.push(text.slice(cursor))
  }
  return <>{fragments}</>
}

function EmptyHelpChecklist() {
  const { t } = useTranslation('agentExperiencePage')
  const reasons = [
    t('help.reasonConnected'),
    t('help.reasonSessions'),
    t('help.reasonCommit'),
  ]

  return (
    <div className="grid max-w-md gap-2 rounded-lg border border-dashed bg-muted/30 px-4 py-3 text-left">
      <p className="text-sm text-muted-foreground">{t('help.title')}</p>
      <ol className="grid gap-1.5 text-sm text-muted-foreground">
        {reasons.map((reason, index) => (
          <li className="flex items-center gap-2" key={reason}>
            <span className="flex size-4 shrink-0 items-center justify-center rounded-full bg-muted text-[10px] font-medium text-muted-foreground">
              {index + 1}
            </span>
            {reason}
          </li>
        ))}
      </ol>
    </div>
  )
}

const EXPERIENCE_PAGE_SIZE_OPTIONS = [10, 20, 50, 100] as const

function ExperiencePagination({
  onPageChange,
  onPageSizeChange,
  page,
  hasMore,
  disabled,
  pageSize,
}: {
  onPageChange: (page: number) => void
  onPageSizeChange: (pageSize: number) => void
  page: number
  hasMore: boolean
  disabled: boolean
  pageSize: number
}) {
  const { t } = useTranslation('agentExperiencePage')

  return (
    <div className="flex flex-col gap-3 border-t border-border/60 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex flex-wrap items-center justify-center gap-3 sm:justify-start">
        <p className="text-sm text-muted-foreground">
          {t('pagination.summary', { page })}
        </p>
        <Select
          disabled={disabled}
          value={String(pageSize)}
          onValueChange={(value) => onPageSizeChange(Number(value))}
        >
          <SelectTrigger size="sm" aria-label={t('pagination.pageSize')}>
            <SelectValue>
              {t('pagination.pageSizeValue', { count: pageSize })}
            </SelectValue>
          </SelectTrigger>
          <SelectContent>
            {EXPERIENCE_PAGE_SIZE_OPTIONS.map((option) => (
              <SelectItem key={option} value={String(option)}>
                {t('pagination.pageSizeValue', { count: option })}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <Pagination className="mx-0 w-auto justify-center sm:justify-end">
        <PaginationContent>
          <PaginationItem>
            <PaginationPrevious
              href="#"
              text={t('pagination.previous')}
              aria-disabled={disabled || page <= 1}
              className={cn(
                (disabled || page <= 1) && 'pointer-events-none opacity-50',
              )}
              onClick={(event) => {
                event.preventDefault()
                if (!disabled && page > 1) onPageChange(page - 1)
              }}
            />
          </PaginationItem>
          <PaginationItem>
            <PaginationNext
              href="#"
              text={t('pagination.next')}
              aria-disabled={disabled || !hasMore}
              className={cn(
                (disabled || !hasMore) && 'pointer-events-none opacity-50',
              )}
              onClick={(event) => {
                event.preventDefault()
                if (!disabled && hasMore) onPageChange(page + 1)
              }}
            />
          </PaginationItem>
        </PaginationContent>
      </Pagination>
    </div>
  )
}

function AgentExperienceRoute() {
  const { t, i18n } = useTranslation('agentExperiencePage')
  const { connection, identityScopeKey } = useAppConnection()
  const [keyword, setKeyword] = React.useState('')
  const [page, setPage] = React.useState(1)
  const [pageSize, setPageSize] = React.useState(10)
  const [previewExperience, setPreviewExperience] =
    React.useState<ExperienceFileItem | null>(null)

  const experiencesUri = buildExperiencesUri(connection.userId)
  const experiencesQuery = useQuery({
    queryFn: ({ signal }) =>
      fetchExperiences({
        experiencesUri,
        page,
        pageSize,
        signal,
      }),
    queryKey: [
      'agent-experience-list',
      identityScopeKey,
      experiencesUri,
      page,
      pageSize,
    ],
    staleTime: 30_000,
  })

  const pageItems = experiencesQuery.data?.items ?? []
  const hasMore = experiencesQuery.data?.hasMore ?? false
  const normalizedKeyword = keyword.trim().toLocaleLowerCase()
  const experiences = pageItems.filter(
    (item) =>
      !normalizedKeyword ||
      item.name.toLocaleLowerCase().includes(normalizedKeyword) ||
      item.uri.toLocaleLowerCase().includes(normalizedKeyword),
  )

  // Snapshot "updated since last visit" badges when the list settles, then
  // mark the whole list as seen. Comparing against the pre-visit snapshot
  // (instead of live state) keeps badges visible for the current visit.
  const [updatedUris, setUpdatedUris] = React.useState<ReadonlySet<string>>(
    () => new Set(),
  )
  const markedRef = React.useRef<string | null>(null)
  React.useEffect(() => {
    if (!experiencesQuery.isSuccess || experiences.length === 0) return
    const fingerprint = `${experiencesUri}:${experiences
      .map((item) => item.uri)
      .join('|')}`
    if (markedRef.current === fingerprint) return
    markedRef.current = fingerprint

    setUpdatedUris(
      new Set(
        experiences
          .filter((experience) =>
            isExperienceUpdatedSinceLastSeen(
              experience.uri,
              experience.modTime,
            ),
          )
          .map((experience) => experience.uri),
      ),
    )
    markExperiencesSeen(experiences)
  }, [experiences, experiencesQuery.isSuccess, experiencesUri])

  const connectionUnavailable =
    isOvClientError(experiencesQuery.error) &&
    experiencesQuery.error.code === 'NETWORK_ERROR'

  const handleOpenPreview = (experience: ExperienceFileItem) => {
    setPreviewExperience(experience)
  }

  return (
    <div className="flex w-full min-w-0 flex-col gap-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="grid gap-1.5">
          <div className="flex items-center gap-2.5">
            <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">
              {t('title')}
            </h1>
          </div>
          <p className="max-w-2xl text-sm leading-6 text-muted-foreground">
            {t('description')}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <EvolutionSettingsPopover />
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={experiencesQuery.isFetching}
            onClick={() => void experiencesQuery.refetch()}
          >
            <RefreshCwIcon
              className={
                experiencesQuery.isFetching ? 'animate-spin' : undefined
              }
            />
            {t('refresh')}
          </Button>
        </div>
      </header>

      {experiencesQuery.isLoading ? (
        <Card className="min-h-56 items-center justify-center">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <LoaderCircleIcon className="size-4 animate-spin" />
            {t('loading')}
          </div>
        </Card>
      ) : experiencesQuery.isError ? (
        <Card className="min-h-56 items-center justify-center px-6 text-center">
          <div className="grid gap-1">
            <p className="font-medium">{t('loadFailed')}</p>
            <p className="max-w-xl text-sm text-muted-foreground">
              {connectionUnavailable
                ? t('networkError')
                : getErrorMessage(experiencesQuery.error)}
            </p>
            {connectionUnavailable ? (
              <Button
                render={<Link to="/settings" />}
                nativeButton={false}
                variant="outline"
                size="sm"
                className="mx-auto mt-2"
              >
                {t('connectionSettings')}
              </Button>
            ) : (
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="mx-auto mt-2"
                disabled={experiencesQuery.isFetching}
                onClick={() => void experiencesQuery.refetch()}
              >
                <RefreshCwIcon
                  className={
                    experiencesQuery.isFetching ? 'animate-spin' : undefined
                  }
                />
                {t('refresh')}
              </Button>
            )}
          </div>
        </Card>
      ) : pageItems.length === 0 &&
        page === 1 &&
        !hasMore &&
        !keyword.trim() ? (
        <Card className="min-h-56 items-center justify-center px-6 text-center">
          <div className="flex size-10 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <BrainCircuitIcon className="size-5" />
          </div>
          <div className="grid max-w-md gap-1">
            <p className="font-medium">{t('empty')}</p>
            <p className="text-sm text-muted-foreground">
              {t('emptyDescription')}
            </p>
            <Button
              render={<Link to="/sessions" />}
              nativeButton={false}
              variant="outline"
              size="sm"
              className="mx-auto mt-3"
            >
              <MessageSquareTextIcon />
              {t('emptyAction')}
            </Button>
          </div>
          <div className="pt-4">
            <EmptyHelpChecklist />
          </div>
        </Card>
      ) : (
        <Card
          size="sm"
          className="rounded-xl bg-background shadow-none ring-border/70 data-[size=sm]:gap-0 data-[size=sm]:py-0"
        >
          <div className="flex flex-wrap items-center justify-end gap-3 border-b border-border/60 px-5 py-4">
            <div className="relative w-full sm:max-w-sm">
              <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                aria-label={t('searchPlaceholder')}
                autoComplete="off"
                className="h-9 bg-transparent pr-9 pl-8 shadow-none dark:bg-transparent"
                name="agent-experience-search"
                placeholder={t('searchPlaceholder')}
                value={keyword}
                onChange={(event) => {
                  setKeyword(event.target.value)
                }}
              />
              {keyword ? (
                <button
                  type="button"
                  aria-label={t('searchClear')}
                  className="absolute top-1/2 right-2 flex size-6 -translate-y-1/2 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                  onClick={() => {
                    setKeyword('')
                  }}
                >
                  <XIcon className="size-3.5" />
                </button>
              ) : null}
            </div>
          </div>

          {experiences.length === 0 ? (
            <div className="grid min-h-40 place-items-center px-6 py-8 text-center">
              <div className="grid max-w-md gap-1">
                <p className="font-medium">{t('searchNoResults')}</p>
                <p className="text-sm text-muted-foreground">
                  {t('searchNoResultsDescription')}
                </p>
              </div>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow className="bg-muted/20 hover:bg-muted/20">
                  <TableHead className="h-10 pl-5 text-xs font-normal text-muted-foreground">
                    {t('columnFile')}
                  </TableHead>
                  <TableHead className="h-10 w-44 text-xs font-normal text-muted-foreground">
                    {t('columnUpdated')}
                  </TableHead>
                  <TableHead className="h-10 w-28 pr-5 text-right text-xs font-normal text-muted-foreground">
                    {t('columnActions')}
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {experiences.map((experience) => {
                  const updated = formatTimestamp(
                    experience.modTime,
                    i18n.language,
                  )
                  const isUpdated = updatedUris.has(experience.uri)

                  return (
                    <TableRow
                      key={experience.uri}
                      className="group cursor-pointer border-border/50 transition-colors hover:bg-muted/30"
                      onClick={() => handleOpenPreview(experience)}
                    >
                      <TableCell className="max-w-0 py-2.5 pl-5">
                        <div className="flex min-w-0 items-center gap-3">
                          <span className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-border/70 text-muted-foreground transition-colors group-hover:border-primary/30 group-hover:text-primary">
                            <FileTextIcon className="size-4" />
                          </span>
                          <div className="grid min-w-0 gap-1">
                            <div className="flex min-w-0 items-center gap-1.5">
                              <button
                                type="button"
                                className="min-w-0 truncate text-left font-medium underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
                                onClick={(event) => {
                                  event.stopPropagation()
                                  handleOpenPreview(experience)
                                }}
                              >
                                <HighlightedText
                                  keyword={normalizedKeyword}
                                  text={experience.name}
                                />
                              </button>
                              {isUpdated ? (
                                <Badge
                                  variant="secondary"
                                  className="h-4 shrink-0 px-1 text-[10px]"
                                >
                                  {t('updatedBadge')}
                                </Badge>
                              ) : null}
                            </div>
                            <span
                              className="truncate font-mono text-[11px] text-muted-foreground/80"
                              title={experience.uri}
                            >
                              <HighlightedText
                                keyword={normalizedKeyword}
                                text={experience.uri}
                              />
                            </span>
                          </div>
                        </div>
                      </TableCell>
                      <TableCell className="w-44 text-xs text-muted-foreground">
                        {updated ?? '-'}
                      </TableCell>
                      <TableCell
                        className="w-28 pr-5 text-right"
                        onClick={(event) => event.stopPropagation()}
                      >
                        <Button
                          render={
                            <Link
                              params={{ experienceUri: experience.uri }}
                              to="/agent-experience/$experienceUri"
                            />
                          }
                          nativeButton={false}
                          size="xs"
                          variant="ghost"
                          className="gap-1 text-muted-foreground hover:text-foreground"
                          aria-label={t('openDetail', {
                            name: experience.name,
                          })}
                        >
                          {t('viewAnalysis')}
                          <ArrowUpRightIcon className="size-3.5" />
                        </Button>
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          )}
          <ExperiencePagination
            page={page}
            hasMore={hasMore}
            disabled={experiencesQuery.isFetching}
            pageSize={pageSize}
            onPageChange={setPage}
            onPageSizeChange={(nextPageSize) => {
              setPageSize(nextPageSize)
              setPage(1)
            }}
          />
        </Card>
      )}

      <ExperiencePreviewSheet
        experience={previewExperience}
        language={i18n.language}
        onClose={() => setPreviewExperience(null)}
      />
    </div>
  )
}
