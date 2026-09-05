import * as React from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, createFileRoute } from '@tanstack/react-router'
import {
  BrainCircuitIcon,
  EyeIcon,
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
  PaginationLink,
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

const EXPERIENCE_PAGE_SIZE_OPTIONS = [20, 50, 100] as const

function ExperiencePagination({
  onPageChange,
  onPageSizeChange,
  page,
  pageCount,
  pageSize,
  total,
}: {
  onPageChange: (page: number) => void
  onPageSizeChange: (pageSize: number) => void
  page: number
  pageCount: number
  pageSize: number
  total: number
}) {
  const { t } = useTranslation('agentExperiencePage')
  const start = Math.max(1, Math.min(page - 2, pageCount - 4))
  const end = Math.min(pageCount, start + 4)
  const pages = Array.from(
    { length: Math.max(0, end - start + 1) },
    (_, index) => start + index,
  )

  return (
    <div className="flex flex-col gap-3 border-t px-5 py-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex flex-wrap items-center justify-center gap-3 sm:justify-start">
        <p className="text-sm text-muted-foreground">
          {t('pagination.summary', { page, pageCount, total })}
        </p>
        <Select
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
              aria-disabled={page <= 1}
              className={cn(page <= 1 && 'pointer-events-none opacity-50')}
              onClick={(event) => {
                event.preventDefault()
                if (page > 1) onPageChange(page - 1)
              }}
            />
          </PaginationItem>
          {pages.map((item) => (
            <PaginationItem key={item}>
              <PaginationLink
                href="#"
                isActive={item === page}
                onClick={(event) => {
                  event.preventDefault()
                  onPageChange(item)
                }}
              >
                {item}
              </PaginationLink>
            </PaginationItem>
          ))}
          <PaginationItem>
            <PaginationNext
              href="#"
              text={t('pagination.next')}
              aria-disabled={page >= pageCount}
              className={cn(
                page >= pageCount && 'pointer-events-none opacity-50',
              )}
              onClick={(event) => {
                event.preventDefault()
                if (page < pageCount) onPageChange(page + 1)
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
  const [pageSize, setPageSize] = React.useState(50)
  const [previewExperience, setPreviewExperience] =
    React.useState<ExperienceFileItem | null>(null)

  const experiencesUri = buildExperiencesUri(connection.userId)
  const experiencesQuery = useQuery({
    placeholderData: (previousData) => previousData,
    queryFn: ({ signal }) =>
      fetchExperiences({
        experiencesUri,
        keyword: keyword.trim(),
        page,
        pageSize,
        signal,
      }),
    queryKey: [
      'agent-experience-list',
      identityScopeKey,
      experiencesUri,
      keyword.trim(),
      page,
      pageSize,
    ],
    staleTime: 30_000,
  })

  const experiences = experiencesQuery.data?.items ?? []
  const total = experiencesQuery.data?.total ?? 0
  const pageCount = Math.max(1, Math.ceil(total / pageSize))
  const normalizedKeyword = keyword.trim().toLocaleLowerCase()

  React.useEffect(() => {
    if (page > pageCount) setPage(pageCount)
  }, [page, pageCount])

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
    <div className="flex w-full min-w-0 flex-col gap-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="grid gap-1.5">
          <div className="flex items-center gap-2.5">
            <h1 className="text-2xl font-semibold tracking-tight">
              {t('title')}
            </h1>
            {total > 0 ? (
              <Badge variant="outline" className="font-normal">
                {total}
              </Badge>
            ) : null}
          </div>
          <p className="max-w-3xl text-sm leading-6 text-muted-foreground">
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
      ) : experiences.length === 0 && !keyword.trim() ? (
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
        <>
          <div className="flex flex-wrap items-center gap-3">
            <div className="relative w-full max-w-sm">
              <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                aria-label={t('searchPlaceholder')}
                autoComplete="off"
                className="pl-8"
                name="agent-experience-search"
                placeholder={t('searchPlaceholder')}
                value={keyword}
                onChange={(event) => {
                  setKeyword(event.target.value)
                  setPage(1)
                }}
              />
              {keyword ? (
                <button
                  type="button"
                  aria-label={t('searchClear')}
                  className="absolute top-1/2 right-2 flex size-6 -translate-y-1/2 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                  onClick={() => {
                    setKeyword('')
                    setPage(1)
                  }}
                >
                  <XIcon className="size-3.5" />
                </button>
              ) : null}
            </div>
            <Badge variant="outline" className="gap-1 font-normal">
              {t('directoryHint')}
            </Badge>
          </div>

          <Card size="sm" className="px-0">
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
                  <TableRow>
                    <TableHead className="pl-5">{t('columnFile')}</TableHead>
                    <TableHead className="w-44">{t('columnUpdated')}</TableHead>
                    <TableHead className="w-28 pr-5 text-right">
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
                        className="cursor-pointer"
                        onClick={() => handleOpenPreview(experience)}
                      >
                        <TableCell className="max-w-0 pl-5">
                          <div className="grid min-w-0 gap-0.5">
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
                            <span className="truncate font-mono text-xs text-muted-foreground">
                              <HighlightedText
                                keyword={normalizedKeyword}
                                text={experience.uri}
                              />
                            </span>
                          </div>
                        </TableCell>
                        <TableCell className="w-44 text-sm text-muted-foreground">
                          {updated ? t('updated', { time: updated }) : '-'}
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
                            variant="outline"
                            aria-label={t('openDetail', {
                              name: experience.name,
                            })}
                          >
                            <EyeIcon className="size-3.5" />
                            {t('viewAnalysis')}
                          </Button>
                        </TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
            )}
            {total > 0 ? (
              <ExperiencePagination
                page={page}
                pageCount={pageCount}
                pageSize={pageSize}
                total={total}
                onPageChange={setPage}
                onPageSizeChange={(nextPageSize) => {
                  setPageSize(nextPageSize)
                  setPage(1)
                }}
              />
            ) : null}
          </Card>
        </>
      )}

      <ExperiencePreviewSheet
        experience={previewExperience}
        language={i18n.language}
        onClose={() => setPreviewExperience(null)}
      />
    </div>
  )
}
