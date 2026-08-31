import {
  Bot,
  ChevronRight,
  CircleAlert,
  FolderOpen,
  RefreshCw,
  Waypoints,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

import type { OkfSidecarMetadata } from '#/lib/okf-markdown'

export function OkfMetadataPanel({
  metadata,
  onNavigate,
  rawFrontmatter,
}: {
  metadata: OkfSidecarMetadata
  onNavigate?: (uri: string) => void
  rawFrontmatter: string
}) {
  const { t } = useTranslation('resources')
  const generatedBy = metadata.generated_by
  const freshness = metadata.freshness
  const source = metadata.source

  return (
    <section
      aria-label={t('filePreview.yamlMetadata.ariaLabel')}
      className="not-prose overflow-hidden rounded-md border bg-muted/10"
    >
      <dl className="grid gap-2.5 p-3 text-xs">
        <div className="grid min-w-0 grid-cols-[7rem_minmax(0,1fr)] items-start gap-3">
          <dt className="flex items-center gap-1.5 pt-0.5 text-muted-foreground">
            <FolderOpen className="size-3.5 shrink-0" />
            {t('filePreview.yamlMetadata.directory')}
          </dt>
          <dd className="min-w-0">
            {onNavigate ? (
              <button
                type="button"
                className="block max-w-full truncate font-mono text-primary underline-offset-4 hover:underline"
                title={metadata.directory}
                onClick={() => onNavigate(metadata.directory)}
              >
                {metadata.directory}
              </button>
            ) : (
              <code
                className="block truncate font-mono text-foreground"
                title={metadata.directory}
              >
                {metadata.directory}
              </code>
            )}
          </dd>
        </div>

        {generatedBy ? (
          <div className="grid min-w-0 grid-cols-[7rem_minmax(0,1fr)] items-start gap-3">
            <dt className="flex items-center gap-1.5 pt-0.5 text-muted-foreground">
              <Bot className="size-3.5 shrink-0" />
              {t('filePreview.yamlMetadata.generatedBy')}
            </dt>
            <dd className="flex min-w-0 flex-wrap items-center gap-1.5">
              <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] text-foreground">
                {generatedBy.component}
              </code>
              <span className="text-muted-foreground">
                {t('filePreview.yamlMetadata.trigger')}
              </span>
              <code className="font-mono text-[11px] text-foreground/80">
                {generatedBy.trigger}
              </code>
            </dd>
          </div>
        ) : null}

        {source ? (
          <div className="grid min-w-0 grid-cols-[7rem_minmax(0,1fr)] items-start gap-3">
            <dt className="flex items-center gap-1.5 pt-0.5 text-muted-foreground">
              <Waypoints className="size-3.5 shrink-0" />
              {t('filePreview.yamlMetadata.source')}
            </dt>
            <dd className="flex min-w-0 items-center gap-1.5">
              <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] font-medium uppercase text-muted-foreground">
                {source.kind}
              </span>
              <code
                className="min-w-0 truncate font-mono text-[11px] text-foreground/80"
                title={source.uri}
              >
                {source.uri}
              </code>
            </dd>
          </div>
        ) : null}
      </dl>

      {freshness ? (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 border-t bg-muted/10 px-3 py-2 text-[11px]">
          <div className="flex items-center gap-1.5 font-medium text-muted-foreground">
            <RefreshCw className="size-3" />
            {t('filePreview.yamlMetadata.freshness')}
          </div>
          <span className="font-mono tabular-nums text-foreground/80">
            {t('filePreview.yamlMetadata.coverage', {
              sampled: freshness.sampled_entries,
              total: freshness.total_entries,
            })}
            <span className="text-muted-foreground">
              {' '}
              ·{' '}
              {t('filePreview.yamlMetadata.unsampled', {
                count: freshness.unsampled_entries,
              })}
            </span>
          </span>
          <span
            data-pending={
              freshness.pending_child_changes > 0 ? 'true' : undefined
            }
            className={
              freshness.pending_child_changes > 0
                ? 'inline-flex items-center gap-1 rounded border border-amber-500/35 bg-amber-500/10 px-1.5 py-0.5 font-mono font-medium tabular-nums text-amber-700 dark:text-amber-300'
                : 'font-mono tabular-nums text-muted-foreground'
            }
          >
            {freshness.pending_child_changes > 0 ? (
              <CircleAlert className="size-3" />
            ) : null}
            {t('filePreview.yamlMetadata.pendingChanges', {
              count: freshness.pending_child_changes,
            })}
          </span>
        </div>
      ) : null}

      <details className="group border-t bg-background/40">
        <summary className="flex cursor-pointer list-none items-center gap-1.5 px-3 py-2 text-[11px] font-medium text-muted-foreground transition-colors marker:hidden hover:bg-muted/40 hover:text-foreground">
          <ChevronRight className="size-3.5 shrink-0 transition-transform group-open:rotate-90" />
          {t('filePreview.yamlMetadata.rawYaml')}
        </summary>
        <div className="border-t bg-background/70 p-3">
          <pre className="max-h-[32rem] min-h-32 overflow-auto whitespace-pre rounded-md border bg-muted/20 p-3 font-mono text-xs leading-5 text-foreground">
            <code>{rawFrontmatter}</code>
          </pre>
        </div>
      </details>
    </section>
  )
}
