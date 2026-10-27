import {
  BracesIcon,
  CableIcon,
  Clock3Icon,
  FileTextIcon,
  Loader2Icon,
  RefreshCwIcon,
  ShieldCheckIcon,
  TagIcon,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import { Badge } from '#/components/ui/badge'
import { Button } from '#/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '#/components/ui/tabs'
import { formatDateTime } from '#/i18n/format'
import { cn } from '#/lib/utils'

import { parseLiveContextMetadata, readLiveContext } from '../-lib/api'
import { formatLiveContextResult } from '../-lib/live-result'
import type { LiveContextMount, McpCatalogTool } from '../-lib/model'

export function LiveContextInspector({
  document,
  mount,
  tool,
}: {
  document: string
  mount: LiveContextMount
  tool?: McpCatalogTool
}) {
  const { t } = useTranslation('arranger')
  const argumentEntries = Object.entries(mount.arguments)
  const annotations = tool?.tool.annotations || {}
  const metadata = parseLiveContextMetadata(document)
  const nodeTitle = metadata?.title || mount.title
  const nodeDescription = metadata?.description || mount.description
  const resultQuery = useQuery({
    enabled: false,
    queryKey: ['arranger-live-result', mount.node_uri],
    queryFn: () => readLiveContext(mount.node_uri),
    retry: false,
  })
  const result =
    resultQuery.data === undefined
      ? null
      : formatLiveContextResult(resultQuery.data)

  return (
    <Tabs defaultValue="result" className="h-full min-h-0 gap-0">
      <TabsList
        variant="line"
        className="h-10 w-full shrink-0 justify-start border-b px-4"
      >
        <TabsTrigger value="result">{t('live.result')}</TabsTrigger>
        <TabsTrigger value="overview">{t('live.overview')}</TabsTrigger>
        <TabsTrigger value="raw">{t('live.rawConfig')}</TabsTrigger>
      </TabsList>
      <TabsContent value="result" className="min-h-0 overflow-y-auto">
        <div className="flex min-h-full flex-col">
          <div className="flex items-center gap-3 border-b p-4">
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium">{t('live.result')}</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {resultQuery.dataUpdatedAt
                  ? t('live.updatedAt', {
                      value: formatDateTime(
                        new Date(resultQuery.dataUpdatedAt),
                        {
                          hour: 'numeric',
                          minute: '2-digit',
                          second: '2-digit',
                        },
                      ),
                    })
                  : t('live.resultDescription')}
              </p>
            </div>
            <Button
              size="sm"
              disabled={resultQuery.isFetching}
              onClick={() => void resultQuery.refetch()}
            >
              {resultQuery.isFetching ? (
                <Loader2Icon className="animate-spin" />
              ) : (
                <RefreshCwIcon />
              )}
              {result ? t('live.refreshResult') : t('live.loadResult')}
            </Button>
          </div>
          {resultQuery.error ? (
            <div className="p-4 text-sm leading-6 text-destructive">
              {resultQuery.error instanceof Error
                ? resultQuery.error.message
                : String(resultQuery.error)}
            </div>
          ) : result ? (
            <pre
              className={cn(
                'min-h-0 flex-1 whitespace-pre-wrap break-words p-4 font-mono text-xs leading-5',
                result.isError && 'text-destructive',
              )}
            >
              {result.text}
            </pre>
          ) : (
            <div className="flex min-h-48 flex-1 items-center justify-center p-6 text-center text-sm text-muted-foreground">
              {t('live.resultEmpty')}
            </div>
          )}
        </div>
      </TabsContent>
      <TabsContent value="overview" className="min-h-0 overflow-y-auto">
        <div className="divide-y">
          {(nodeTitle || nodeDescription) && (
            <section className="space-y-3 p-4">
              <InspectorHeading
                icon={FileTextIcon}
                label={t('live.nodeInfo')}
              />
              <div>
                {nodeTitle && (
                  <p className="text-sm font-medium">{nodeTitle}</p>
                )}
                {nodeDescription && (
                  <p className="mt-1 text-xs leading-5 text-muted-foreground">
                    {nodeDescription}
                  </p>
                )}
              </div>
            </section>
          )}

          <section className="space-y-3 p-4">
            <InspectorHeading icon={CableIcon} label={t('live.boundTool')} />
            <div className="grid grid-cols-[5rem_minmax(0,1fr)] gap-x-3 gap-y-2 text-xs">
              <span className="text-muted-foreground">
                {t('fields.server')}
              </span>
              <span className="truncate font-mono">{mount.mcp}</span>
              <span className="text-muted-foreground">{t('fields.tool')}</span>
              <span className="truncate font-mono">{mount.tool}</span>
            </div>
          </section>

          <section className="space-y-3 p-4">
            <InspectorHeading icon={BracesIcon} label={t('fields.arguments')} />
            {argumentEntries.length ? (
              <dl className="divide-y rounded-md border">
                {argumentEntries.map(([name, value]) => (
                  <div
                    key={name}
                    className="grid grid-cols-[minmax(5rem,0.35fr)_minmax(0,1fr)] gap-3 px-3 py-2 text-xs"
                  >
                    <dt className="truncate font-mono text-muted-foreground">
                      {name}
                    </dt>
                    <dd className="break-all font-mono">
                      {typeof value === 'string'
                        ? value
                        : JSON.stringify(value)}
                    </dd>
                  </div>
                ))}
              </dl>
            ) : (
              <p className="text-xs text-muted-foreground">
                {t('mount.noArguments')}
              </p>
            )}
          </section>

          <section className="space-y-3 p-4">
            <InspectorHeading icon={Clock3Icon} label={t('live.runtime')} />
            <div className="flex flex-wrap gap-2">
              <Badge variant="outline">
                {t('live.timeoutValue', {
                  value: mount.timeout_seconds || 60,
                })}
              </Badge>
              {annotations.readOnlyHint === true && (
                <Badge variant="outline">
                  <ShieldCheckIcon />
                  {t('live.readOnlyHint')}
                </Badge>
              )}
              {annotations.destructiveHint === true && (
                <Badge variant="destructive">{t('live.destructiveHint')}</Badge>
              )}
            </div>
          </section>

          {mount.tags && mount.tags.length > 0 && (
            <section className="space-y-3 p-4">
              <InspectorHeading icon={TagIcon} label={t('fields.tags')} />
              <div className="flex flex-wrap gap-1.5">
                {mount.tags.map((tag) => (
                  <Badge key={tag} variant="secondary">
                    {tag}
                  </Badge>
                ))}
              </div>
            </section>
          )}
        </div>
      </TabsContent>
      <TabsContent value="raw" className="min-h-0 overflow-auto">
        <pre className="min-h-full whitespace-pre-wrap break-words p-4 font-mono text-xs leading-5 text-foreground">
          {document}
        </pre>
      </TabsContent>
    </Tabs>
  )
}

function InspectorHeading({
  icon: Icon,
  label,
}: {
  icon: LucideIcon
  label: string
}) {
  return (
    <div className="flex items-center gap-2 text-xs font-semibold">
      <Icon className="size-3.5 text-muted-foreground" />
      <span>{label}</span>
    </div>
  )
}
