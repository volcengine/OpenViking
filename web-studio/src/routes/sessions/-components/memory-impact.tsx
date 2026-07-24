import { useMemo, useState } from 'react'
import {
  BrainCircuitIcon,
  FilePenLineIcon,
  FilePlus2Icon,
  FileX2Icon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Badge } from '#/components/ui/badge'
import { Button } from '#/components/ui/button'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '#/components/ui/sheet'
import type {
  MemoryDiffKind,
  SessionMemoryDiff,
  SessionMemoryDiffOperation,
} from '#/lib/sessions/memory-diff'

interface MemoryImpactProps {
  diffs: SessionMemoryDiff[]
}

const KIND_STYLES: Record<
  MemoryDiffKind,
  { icon: typeof FilePlus2Icon; text: string }
> = {
  add: {
    icon: FilePlus2Icon,
    text: 'text-emerald-600 dark:text-emerald-400',
  },
  update: {
    icon: FilePenLineIcon,
    text: 'text-amber-600 dark:text-amber-400',
  },
  delete: {
    icon: FileX2Icon,
    text: 'text-rose-600 dark:text-rose-400',
  },
}

export function MemoryImpact({ diffs }: MemoryImpactProps) {
  const { i18n, t } = useTranslation('sessions')
  const [open, setOpen] = useState(false)
  const totals = useMemo(
    () =>
      diffs.reduce(
        (result, diff) => ({
          adds: result.adds + diff.summary.adds,
          deletes: result.deletes + diff.summary.deletes,
          updates: result.updates + diff.summary.updates,
        }),
        { adds: 0, deletes: 0, updates: 0 },
      ),
    [diffs],
  )
  const totalChanges = totals.adds + totals.updates + totals.deletes

  if (totalChanges === 0) return null

  return (
    <>
      <Button
        aria-label={t('impact.open')}
        className="h-7 gap-1.5 rounded-full border-primary/20 bg-primary/5 px-2.5 text-xs text-primary hover:bg-primary/10"
        onClick={() => setOpen(true)}
        size="xs"
        variant="outline"
      >
        <BrainCircuitIcon className="size-3.5" />
        <span>{t('impact.title')}</span>
        <ImpactCounts totals={totals} />
      </Button>

      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent className="gap-0 sm:max-w-2xl">
          <SheetHeader className="border-b px-6 py-5">
            <div className="flex items-center gap-3 pr-10">
              <div className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary ring-1 ring-primary/15">
                <BrainCircuitIcon className="size-4.5" />
              </div>
              <div className="min-w-0">
                <SheetTitle className="text-lg">{t('impact.title')}</SheetTitle>
                <SheetDescription>
                  {t('impact.description', {
                    changes: totalChanges,
                    commits: diffs.length,
                  })}
                </SheetDescription>
              </div>
            </div>
          </SheetHeader>

          <div className="grid grid-cols-3 gap-2 border-b bg-muted/20 px-6 py-4">
            <SummaryMetric
              kind="add"
              label={t('impact.kinds.add')}
              value={totals.adds}
            />
            <SummaryMetric
              kind="update"
              label={t('impact.kinds.update')}
              value={totals.updates}
            />
            <SummaryMetric
              kind="delete"
              label={t('impact.kinds.delete')}
              value={totals.deletes}
            />
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
            <div className="space-y-6">
              {diffs.map((diff) => (
                <section key={diff.archiveId} className="space-y-2.5">
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex min-w-0 items-center gap-2">
                      <code className="text-xs font-medium text-foreground">
                        {diff.archiveId}
                      </code>
                      <ImpactCounts totals={diff.summary} />
                    </div>
                    {diff.extractedAt ? (
                      <time className="shrink-0 text-xs text-muted-foreground">
                        {formatDate(diff.extractedAt, i18n.resolvedLanguage)}
                      </time>
                    ) : null}
                  </div>

                  <div className="overflow-hidden rounded-xl border bg-background">
                    {diff.operations.map((operation, index) => (
                      <OperationRow
                        key={`${operation.kind}-${operation.uri}-${index}`}
                        operation={operation}
                      />
                    ))}
                  </div>
                </section>
              ))}
            </div>
          </div>
        </SheetContent>
      </Sheet>
    </>
  )
}

function ImpactCounts({
  totals,
}: {
  totals: { adds: number; deletes: number; updates: number }
}) {
  return (
    <span className="flex items-center gap-1 font-mono text-[10px]">
      {totals.adds > 0 ? (
        <span className="text-emerald-600 dark:text-emerald-400">
          +{totals.adds}
        </span>
      ) : null}
      {totals.updates > 0 ? (
        <span className="text-amber-600 dark:text-amber-400">
          ~{totals.updates}
        </span>
      ) : null}
      {totals.deletes > 0 ? (
        <span className="text-rose-600 dark:text-rose-400">
          −{totals.deletes}
        </span>
      ) : null}
    </span>
  )
}

function SummaryMetric({
  kind,
  label,
  value,
}: {
  kind: MemoryDiffKind
  label: string
  value: number
}) {
  const { icon: Icon, text } = KIND_STYLES[kind]

  return (
    <div className="rounded-xl border bg-background px-3 py-2.5">
      <div className={`flex items-center gap-1.5 text-xs ${text}`}>
        <Icon className="size-3.5" />
        {label}
      </div>
      <div className="mt-1 font-mono text-xl font-medium text-foreground">
        {value}
      </div>
    </div>
  )
}

function OperationRow({
  operation,
}: {
  operation: SessionMemoryDiffOperation
}) {
  const { t } = useTranslation('sessions')
  const { icon: Icon, text } = KIND_STYLES[operation.kind]

  return (
    <details className="group border-b last:border-b-0">
      <summary className="flex cursor-pointer list-none items-center gap-3 px-3.5 py-3 transition-colors hover:bg-muted/40">
        <Icon className={`size-4 shrink-0 ${text}`} />
        <code className="min-w-0 flex-1 truncate text-xs text-foreground">
          {operation.uri}
        </code>
        <Badge className="max-w-28" variant="outline">
          <span className="truncate">{operation.memoryType}</span>
        </Badge>
        <span className="text-[10px] text-muted-foreground transition-transform group-open:rotate-90">
          ›
        </span>
      </summary>

      <div className="border-t bg-muted/15 px-3.5 py-3">
        {operation.kind === 'update' ? (
          <div className="grid gap-3 sm:grid-cols-2">
            <ContentBlock
              content={operation.before}
              label={t('impact.before')}
              tone="delete"
            />
            <ContentBlock
              content={operation.after}
              label={t('impact.after')}
              tone="add"
            />
          </div>
        ) : (
          <ContentBlock
            content={operation.after}
            label={t(
              operation.kind === 'add'
                ? 'impact.addedContent'
                : 'impact.deletedContent',
            )}
            tone={operation.kind}
          />
        )}
      </div>
    </details>
  )
}

function ContentBlock({
  content,
  label,
  tone,
}: {
  content?: string
  label: string
  tone: 'add' | 'delete'
}) {
  const { t } = useTranslation('sessions')

  return (
    <div className="min-w-0">
      <div
        className={`mb-1.5 text-[11px] font-medium ${
          tone === 'add'
            ? 'text-emerald-600 dark:text-emerald-400'
            : 'text-rose-600 dark:text-rose-400'
        }`}
      >
        {label}
      </div>
      <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-lg border bg-background/80 p-3 font-mono text-[11px] leading-5 text-muted-foreground">
        {content || t('impact.emptyContent')}
      </pre>
    </div>
  )
}

function formatDate(value: string, language?: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat(language, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date)
}
