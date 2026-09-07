import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { BarChart3Icon, LoaderCircleIcon } from 'lucide-react'
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
import { useAppConnection } from '#/hooks/use-app-connection'

import { fetchContent } from '../-lib/api'
import { formatTimestamp } from '../-lib/experience'
import type { ExperienceFileItem } from '../-lib/types'

/** Quick preview of an experience without leaving the list page. */
export function ExperiencePreviewSheet({
  experience,
  language,
  onClose,
}: {
  experience: ExperienceFileItem | null
  language: string
  onClose: () => void
}) {
  const { t } = useTranslation('agentExperiencePage')
  const { identityScopeKey } = useAppConnection()
  const contentQuery = useQuery({
    enabled: Boolean(experience),
    queryFn: ({ signal }) => fetchContent(experience?.uri ?? '', signal),
    queryKey: ['agent-experience-content', identityScopeKey, experience?.uri],
    staleTime: 60_000,
  })

  const updated = formatTimestamp(experience?.modTime, language)

  return (
    <Sheet
      open={Boolean(experience)}
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
    >
      <SheetContent className="gap-0 data-[side=right]:sm:max-w-2xl">
        <SheetHeader className="border-b px-6 py-5">
          <div className="flex items-center gap-2 pr-10">
            <SheetTitle className="truncate text-lg">
              {experience?.name ?? t('title')}
            </SheetTitle>
          </div>
          <SheetDescription className="pr-10">
            <span className="block truncate font-mono text-xs">
              {experience?.uri}
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
              {t('detail.contentLoading')}
            </div>
          ) : contentQuery.isError ? (
            <div className="grid min-h-48 place-items-center gap-2 text-center">
              <p className="font-medium">{t('detail.contentLoadFailed')}</p>
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

        {experience ? (
          <div className="flex justify-end border-t px-6 py-4">
            <Button
              render={
                <Link
                  params={{ experienceUri: experience.uri }}
                  to="/agent-experience/$experienceUri"
                />
              }
              nativeButton={false}
              size="sm"
              onClick={onClose}
            >
              <BarChart3Icon className="size-4" />
              {t('viewAnalysis')}
            </Button>
          </div>
        ) : null}
      </SheetContent>
    </Sheet>
  )
}
