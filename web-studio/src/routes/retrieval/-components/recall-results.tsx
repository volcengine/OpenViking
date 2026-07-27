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

import type { MemoryDetail } from './memory-detail-sheet'
import { displayName } from '../-lib/results'

export function RecallResults({
  onSelect,
  result,
  t,
}: {
  onSelect: (detail: MemoryDetail) => void
  result: RecallResult
  t: TFunction<'retrieval'>
}) {
  const [view, setView] = useState<'entries' | 'rendered'>('entries')
  const [copied, setCopied] = useState(false)

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
            variant={view === 'entries' ? 'secondary' : 'ghost'}
          >
            {t('recall.entries')} ({result.entries.length})
          </Button>
          <Button
            disabled={!result.rendered}
            onClick={() => setView('rendered')}
            size="sm"
            variant={view === 'rendered' ? 'secondary' : 'ghost'}
          >
            {t('recall.rendered')}
          </Button>
        </div>
        {view === 'rendered' && result.rendered ? (
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

      {view === 'rendered' ? (
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
  onSelect: (detail: MemoryDetail) => void
  t: TFunction<'retrieval'>
}) {
  const { name, parent } = displayName(entry.uri)

  return (
    <button
      className="flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:ring-inset"
      onClick={() =>
        onSelect({
          abstract: entry.abstract,
          content: entry.content,
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
      <div className="mt-0.5 inline-flex shrink-0 items-center gap-1 rounded-md bg-amber-500/15 px-2 py-1 text-[11px] font-semibold uppercase tracking-wide text-amber-500">
        <Brain className="size-3" />
        {entry.type}
      </div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium">{name}</div>
        <div className="mt-0.5 truncate text-xs text-muted-foreground/70">
          {parent}
        </div>
        {entry.summary || entry.abstract ? (
          <p className="mt-1 line-clamp-2 text-xs text-muted-foreground/60">
            {entry.summary ?? entry.abstract}
          </p>
        ) : null}
      </div>
      <div className="flex shrink-0 items-center gap-1.5">
        {entry.origin ? <Badge variant="outline">{entry.origin}</Badge> : null}
        <Badge variant="secondary">{entry.mode}</Badge>
        <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] tabular-nums text-muted-foreground">
          {entry.score.toFixed(3)}
        </span>
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
