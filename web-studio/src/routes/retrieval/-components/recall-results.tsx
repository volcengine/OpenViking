import {
  Brain,
  Check,
  Clipboard,
  Database,
  FileWarning,
  Layers3,
} from 'lucide-react'
import { useState } from 'react'
import type { TFunction } from 'i18next'

import { Badge } from '#/components/ui/badge'
import { Button } from '#/components/ui/button'
import type { RecallEntry, RecallResult } from '#/lib/retrieval'

import type { RetrievalDetail } from './retrieval-detail-sheet'
import { displayName } from '../-lib/results'

export function RecallResults({
  onSelect,
  result,
  t,
}: {
  onSelect: (detail: RetrievalDetail) => void
  result: RecallResult
  t: TFunction<'retrieval'>
}) {
  const [view, setView] = useState<'entries' | 'rendered'>('entries')
  const [copied, setCopied] = useState(false)
  const activeView = view === 'rendered' && !result.rendered ? 'entries' : view

  const copyRendered = async () => {
    await navigator.clipboard.writeText(result.rendered)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1600)
  }

  return (
    <>
      <div className="grid gap-2 border-b bg-muted/20 p-4 sm:grid-cols-2 xl:grid-cols-4">
        <RecallMetric
          icon={Database}
          label={t('recall.stats.returned')}
          value={result.stats.returned}
        />
        <RecallMetric
          icon={FileWarning}
          label={t('recall.stats.dropped')}
          value={result.stats.dropped}
        />
        <RecallMetric
          icon={Layers3}
          label={t('recall.stats.maxChars')}
          value={result.stats.max_chars}
        />
        <RecallMetric
          icon={Brain}
          label={t('recall.stats.scope')}
          value={t(`recall.peerScopes.${result.stats.peer_scope}`)}
        />
      </div>

      <div className="flex items-center justify-between border-b px-4 py-2">
        <div className="flex gap-1">
          <Button
            onClick={() => setView('entries')}
            size="sm"
            variant={activeView === 'entries' ? 'secondary' : 'ghost'}
          >
            {t('recall.entries')} ({result.entries.length})
          </Button>
          <Button
            disabled={!result.rendered}
            onClick={() => setView('rendered')}
            size="sm"
            variant={activeView === 'rendered' ? 'secondary' : 'ghost'}
          >
            {t('recall.rendered')}
          </Button>
        </div>
        {activeView === 'rendered' && result.rendered ? (
          <Button
            className="gap-1.5"
            onClick={() => void copyRendered()}
            size="sm"
            variant="outline"
          >
            {copied ? (
              <Check className="size-3.5" />
            ) : (
              <Clipboard className="size-3.5" />
            )}
            {copied ? t('recall.copied') : t('recall.copy')}
          </Button>
        ) : null}
      </div>

      {activeView === 'rendered' ? (
        <pre className="max-h-[36rem] overflow-auto whitespace-pre-wrap break-words p-5 font-mono text-xs leading-6">
          {result.rendered}
        </pre>
      ) : (
        <div className="divide-y">
          {result.entries.map((entry, index) => (
            <RecallRow
              entry={entry}
              key={`${entry.uri}-${entry.type}-${index}`}
              onSelect={onSelect}
              t={t}
            />
          ))}
        </div>
      )}
    </>
  )
}

function RecallRow({
  entry,
  onSelect,
  t,
}: {
  entry: RecallEntry
  onSelect: (detail: RetrievalDetail) => void
  t: TFunction<'retrieval'>
}) {
  const { name } = displayName(entry.uri)

  return (
    <button
      className="group w-full px-5 py-4 text-left transition-colors hover:bg-muted/35 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:ring-inset"
      onClick={() =>
        onSelect({
          abstract: entry.abstract,
          content: entry.content,
          contextType: 'memory',
          memoryType: entry.type.toUpperCase(),
          mode: entry.mode,
          origin: entry.origin,
          rank: entry.rank,
          score: entry.score,
          summary: entry.summary,
          uri: entry.uri,
        })
      }
      type="button"
    >
      <div className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] gap-x-5">
        <div className="min-w-0">
          <div className="flex min-w-0 items-center gap-2.5">
            <h3 className="min-w-0 truncate text-sm font-semibold text-foreground">
              {name}
            </h3>
            <div className="flex shrink-0 items-center gap-1.5">
              <span className="inline-flex items-center gap-1 font-mono text-[10px] font-medium uppercase tracking-wide text-amber-500">
                <Brain className="size-3" />
                {entry.type}
              </span>
              {entry.origin ? (
                <Badge className="h-6 text-[10px]" variant="outline">
                  {entry.origin}
                </Badge>
              ) : null}
              <Badge className="h-6 text-[10px]" variant="secondary">
                {entry.mode}
              </Badge>
            </div>
          </div>

          <dl className="mt-2.5 grid min-w-0 grid-cols-[3.25rem_minmax(0,1fr)] gap-x-2.5 gap-y-1.5">
            <dt className="pt-px text-[10px] font-medium uppercase tracking-[0.1em] text-muted-foreground/55">
              {t('results.uri')}
            </dt>
            <dd
              className="min-w-0 truncate font-mono text-[11px] text-muted-foreground/75"
              title={entry.uri}
            >
              {entry.uri}
            </dd>

            {entry.summary || entry.abstract ? (
              <>
                <dt className="pt-0.5 text-[10px] font-medium uppercase tracking-[0.1em] text-muted-foreground/55">
                  {t('results.description')}
                </dt>
                <dd className="line-clamp-2 text-xs leading-5 text-muted-foreground/70">
                  {entry.summary ?? entry.abstract}
                </dd>
              </>
            ) : null}
          </dl>
        </div>

        <div className="min-w-16 self-start rounded-md border border-border/60 bg-muted/35 px-2.5 py-2 text-right transition-colors group-hover:bg-background/70">
          <div className="text-[9px] font-medium uppercase tracking-[0.1em] text-muted-foreground/50">
            {t('results.score')}
          </div>
          <div className="mt-0.5 font-mono text-xs font-semibold tabular-nums text-foreground/75">
            {entry.score.toFixed(3)}
          </div>
        </div>
      </div>
    </button>
  )
}

function RecallMetric({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Brain
  label: string
  value: number | string
}) {
  return (
    <div className="rounded-lg border bg-background px-3 py-2.5">
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <Icon className="size-3.5" />
        {label}
      </div>
      <div className="mt-1 font-mono text-lg font-medium">{value}</div>
    </div>
  )
}
