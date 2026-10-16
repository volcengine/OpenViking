import { useQuery } from '@tanstack/react-query'
import { LoaderCircleIcon, RouteIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { Button } from '#/components/ui/button'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '#/components/ui/sheet'
import { fetchTrajectoryContent } from '../-lib/api'
import { formatTimestamp } from '../-lib/experience'
import type { TrajectoryItem } from '../-lib/types'

/** Side sheet that renders the trajectory markdown content. */
export function TrajectoryPreviewSheet({
  language,
  onClose,
  trajectory,
}: {
  language: string
  onClose: () => void
  trajectory: TrajectoryItem | null
}) {
  const { t } = useTranslation('agentExperiencePage')
  const contentQuery = useQuery({
    enabled: Boolean(trajectory),
    queryFn: ({ signal }) =>
      fetchTrajectoryContent(trajectory?.uri ?? '', signal),
    queryKey: ['agent-experience-trajectory-content', trajectory?.uri],
    staleTime: 60_000,
  })

  const updated = formatTimestamp(
    trajectory?.updated_at ?? trajectory?.created_at,
    language,
  )

  return (
    <Sheet
      open={Boolean(trajectory)}
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
    >
      <SheetContent className="gap-0 data-[side=right]:sm:max-w-2xl">
        <SheetHeader className="border-b px-6 py-5">
          <div className="flex items-center gap-2 pr-10">
            <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <RouteIcon className="size-4" />
            </div>
            <SheetTitle className="truncate text-lg">
              {trajectory?.name ?? t('detail.previewTitle')}
            </SheetTitle>
          </div>
          <SheetDescription className="pr-10">
            <span className="block truncate font-mono text-xs">
              {trajectory?.uri}
            </span>
            {updated ? (
              <span className="block pt-1 text-xs">
                {t('updated', { time: updated })}
              </span>
            ) : null}
          </SheetDescription>
        </SheetHeader>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
          {contentQuery.isLoading ? (
            <div className="flex min-h-48 items-center justify-center gap-2 text-sm text-muted-foreground">
              <LoaderCircleIcon className="size-4 animate-spin" />
              {t('detail.previewLoading')}
            </div>
          ) : contentQuery.isError ? (
            <div className="grid min-h-48 place-items-center gap-2 text-center">
              <p className="font-medium">{t('detail.previewLoadFailed')}</p>
              <Button
                type="button"
                size="xs"
                variant="outline"
                onClick={() => void contentQuery.refetch()}
              >
                {t('refresh')}
              </Button>
            </div>
          ) : (
            <div className="prose prose-sm max-w-none break-words dark:prose-invert dark:prose-pre:bg-muted-foreground/20">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {contentQuery.data || ''}
              </ReactMarkdown>
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}
