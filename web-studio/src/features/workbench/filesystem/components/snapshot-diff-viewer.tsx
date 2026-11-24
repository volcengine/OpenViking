import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'

import { parseSnapshotDiff } from '#/features/workbench/filesystem/lib/snapshot-diff'

interface Props {
  diff: string
  missingBase?: boolean
  missingTarget?: boolean
}

export function SnapshotDiffViewer({
  diff,
  missingBase = false,
  missingTarget = false,
}: Props) {
  const { t } = useTranslation('dataManagement')
  const hunks = useMemo(() => parseSnapshotDiff(diff), [diff])
  const rows = hunks.flatMap((hunk) => hunk.rows)
  const added = rows.filter(
    (row) => row.newText !== undefined && row.kind !== 'context',
  ).length
  const removed = rows.filter(
    (row) => row.oldText !== undefined && row.kind !== 'context',
  ).length

  return (
    <section
      aria-label={t('diffComparison')}
      className="flex h-full min-h-0 min-w-0 flex-col gap-3"
    >
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 text-sm">
        <h3 className="font-medium">{t('diffComparison')}</h3>
        <div className="flex gap-3 font-mono text-xs tabular-nums">
          <span className="text-emerald-700 dark:text-emerald-400">
            +{added}
          </span>
          <span className="text-red-700 dark:text-red-400">−{removed}</span>
        </div>
      </div>
      {(missingBase || missingTarget) && (
        <div className="grid shrink-0 gap-2 text-xs md:grid-cols-2">
          <div>
            {missingBase && (
              <span className="inline-block rounded border border-muted-foreground/30 bg-muted px-2 py-1 text-muted-foreground">
                {t('versionMissingFile')}
              </span>
            )}
          </div>
          <div>
            {missingTarget && (
              <span className="inline-block rounded border border-muted-foreground/30 bg-muted px-2 py-1 text-muted-foreground">
                {t('versionMissingFile')}
              </span>
            )}
          </div>
        </div>
      )}
      {hunks.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t('noChanges')}</p>
      ) : (
        <div className="min-h-0 min-w-0 flex-1 overflow-y-auto rounded-md border">
          <div className="sticky top-0 z-10 hidden grid-cols-2 border-b bg-muted text-xs font-medium md:grid">
            <div className="border-r px-3 py-2">{t('previousVersion')}</div>
            <div className="px-3 py-2">{t('selectedVersion')}</div>
          </div>
          {hunks.map((hunk, hunkIndex) => (
            <div key={hunkIndex}>
              <div className="border-y bg-muted/70 px-3 py-1.5 font-mono text-xs text-muted-foreground">
                {hunk.label || t('diffSection', { number: hunkIndex + 1 })}
              </div>
              {hunk.rows.map((row, rowIndex) => (
                <div
                  className="grid min-w-0 grid-cols-1 border-b border-border/50 font-mono text-xs leading-5 last:border-b-0 md:grid-cols-2"
                  key={rowIndex}
                >
                  <div
                    className={`flex min-w-0 border-r px-2 py-0.5 ${row.oldText === undefined ? 'hidden md:flex' : ''} ${row.kind === 'removed' || row.kind === 'changed' ? 'bg-red-500/10' : ''}`}
                  >
                    <span
                      aria-hidden="true"
                      className="w-9 shrink-0 select-none text-right text-muted-foreground"
                    >
                      {row.oldLine}
                    </span>
                    <span className="min-w-0 flex-1 whitespace-pre-wrap break-all pl-3">
                      {row.oldText}
                    </span>
                  </div>
                  <div
                    className={`flex min-w-0 px-2 py-0.5 ${row.newText === undefined ? 'hidden md:flex' : ''} ${row.kind === 'added' || row.kind === 'changed' ? 'bg-emerald-500/10' : ''}`}
                  >
                    <span
                      aria-hidden="true"
                      className="w-9 shrink-0 select-none text-right text-muted-foreground"
                    >
                      {row.newLine}
                    </span>
                    <span className="min-w-0 flex-1 whitespace-pre-wrap break-all pl-3">
                      {row.newText}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
