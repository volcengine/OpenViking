import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { Brain, ExternalLink, FileText, FolderOpen, Wrench } from 'lucide-react'
import type { TFunction } from 'i18next'

import { Badge } from '#/components/ui/badge'
import { Button } from '#/components/ui/button'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '#/components/ui/sheet'
import { cn } from '#/lib/utils'
import { fetchFileContent } from '#/routes/resources/-lib/api'

import {
  displayName,
  isDuplicateDisplayContent,
  resourceSearchForResult,
} from '../-lib/results'
import type { FindContextType, FindResultItem } from '#/lib/retrieval'

const DETAIL_TYPE_META: Record<
  FindContextType,
  { icon: typeof Brain; color: string }
> = {
  resource: {
    icon: FileText,
    color: 'text-blue-500',
  },
  memory: {
    icon: Brain,
    color: 'text-amber-500',
  },
  skill: {
    icon: Wrench,
    color: 'text-emerald-500',
  },
}

export interface RetrievalDetail {
  abstract?: string
  content?: string
  contextType: FindContextType
  item?: FindResultItem
  memoryType?: string
  mode?: string
  origin?: string
  rank?: number
  score: number
  summary?: string
  uri: string
}

export function RetrievalDetailSheet({
  detail,
  onClose,
  t,
}: {
  detail: RetrievalDetail | null
  onClose: () => void
  t: TFunction<'retrieval'>
}) {
  const contentQuery = useQuery({
    enabled: Boolean(detail && !detail.content),
    queryFn: () => fetchFileContent(detail!.uri),
    queryKey: ['retrieval-memory-content', detail?.uri],
    staleTime: 5 * 60_000,
  })
  const { name, parent } = displayName(detail?.uri ?? '')
  const content = detail?.content ?? contentQuery.data?.content
  const summary = detail?.summary ?? detail?.abstract
  const showSummary =
    Boolean(summary) && !isDuplicateDisplayContent(summary, content)
  const resourceSearch = detail?.item
    ? resourceSearchForResult(detail.item)
    : undefined
  const typeMeta = DETAIL_TYPE_META[detail?.contextType ?? 'memory']
  const TypeIcon = typeMeta.icon

  return (
    <Sheet open={detail !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="gap-0 data-[side=right]:sm:max-w-3xl">
        <SheetHeader className="border-b px-6 py-5">
          <div className="flex items-start gap-3 pr-10">
            <div
              className={cn(
                'flex size-6 shrink-0 items-center justify-center',
                typeMeta.color,
              )}
            >
              <TypeIcon className="size-4" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <SheetTitle className="truncate text-lg">{name}</SheetTitle>
                {detail?.memoryType ? (
                  <Badge
                    className="h-6 bg-transparent font-mono text-[10px] font-medium text-muted-foreground"
                    variant="outline"
                  >
                    {detail.memoryType}
                  </Badge>
                ) : null}
              </div>
              <SheetDescription className="mt-1 flex items-center gap-1.5">
                <FolderOpen className="size-3" />
                <span className="truncate">{parent}</span>
              </SheetDescription>
            </div>
          </div>
        </SheetHeader>

        <div className="flex flex-wrap items-center gap-2 border-b bg-muted/20 px-6 py-3">
          <MetaBadge
            label={t('detail.score')}
            value={detail?.score.toFixed(3) ?? '0'}
          />
          {detail?.item ? (
            <MetaText
              label={t('detail.level')}
              value={`L${detail.item.level}`}
            />
          ) : null}
          {detail?.rank ? (
            <MetaBadge label={t('detail.rank')} value={String(detail.rank)} />
          ) : null}
          {detail?.origin ? (
            <MetaBadge label={t('detail.origin')} value={detail.origin} />
          ) : null}
          {detail?.mode ? (
            <MetaBadge label={t('detail.mode')} value={detail.mode} />
          ) : null}
          {resourceSearch ? (
            <Button
              className="ml-auto gap-1.5"
              nativeButton={false}
              render={
                <Link
                  rel="noreferrer noopener"
                  search={resourceSearch}
                  target="_blank"
                  to="/playground"
                />
              }
              size="sm"
              variant="outline"
            >
              {t('detail.openPlayground')}
              <ExternalLink className="size-3.5" />
            </Button>
          ) : null}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
          <section className="space-y-2">
            <h3 className="text-xs font-medium text-muted-foreground">
              {t('detail.uri')}
            </h3>
            <code className="block break-all rounded-lg border bg-muted/30 px-3 py-2 text-xs">
              {detail?.uri}
            </code>
          </section>

          {showSummary ? (
            <section className="mt-5 space-y-2">
              <h3 className="text-xs font-medium text-muted-foreground">
                {t('detail.summary')}
              </h3>
              <p className="whitespace-pre-wrap text-sm leading-6">{summary}</p>
            </section>
          ) : null}

          <section className="mt-5 space-y-2">
            <h3 className="text-xs font-medium text-muted-foreground">
              {t('detail.content')}
            </h3>
            {contentQuery.isLoading && !detail?.content ? (
              <div className="rounded-lg border bg-muted/20 px-4 py-8 text-center text-sm text-muted-foreground">
                {t('detail.loading')}
              </div>
            ) : contentQuery.isError && !detail?.content ? (
              <div className="rounded-lg border border-destructive/20 bg-destructive/5 px-4 py-6 text-center">
                <p className="text-sm text-destructive">
                  {t('detail.loadFailed')}
                </p>
                <Button
                  className="mt-3"
                  onClick={() => void contentQuery.refetch()}
                  size="sm"
                  variant="outline"
                >
                  {t('detail.retry')}
                </Button>
              </div>
            ) : (
              <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded-lg border bg-muted/20 p-4 font-mono text-xs leading-6">
                {content || t('detail.noContent')}
              </pre>
            )}
          </section>

          {detail?.item?.match_reason ? (
            <section className="mt-5 space-y-2">
              <h3 className="text-xs font-medium text-muted-foreground">
                {t('detail.matchReason')}
              </h3>
              <p className="text-sm">{detail.item.match_reason}</p>
            </section>
          ) : null}
        </div>
      </SheetContent>
    </Sheet>
  )
}

function MetaBadge({ label, value }: { label: string; value: string }) {
  return (
    <span className="rounded-md border bg-background px-2 py-1 font-mono text-[11px] text-muted-foreground">
      {label}: <span className="text-foreground">{value}</span>
    </span>
  )
}

function MetaText({ label, value }: { label: string; value: string }) {
  return (
    <span className="px-1 font-mono text-[11px] text-muted-foreground/50">
      {label}
      <span className="ml-1 text-muted-foreground/75">{value}</span>
    </span>
  )
}
