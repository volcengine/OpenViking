import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { formatDateTime } from '#/i18n/format'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '#/shared/ui/dialog'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '#/shared/ui/select'

import { SnapshotDiffViewer } from '#/features/workbench/filesystem/components/snapshot-diff-viewer'
import type {
  SnapshotCommit,
  SnapshotComparison,
} from '#/features/workbench/filesystem/lib/data-management'
import {
  createDataManagementClient,
  diffSnapshot,
  diffSnapshotWithCurrent,
  listSnapshots,
} from '#/features/workbench/filesystem/lib/data-management'

interface Props {
  onClose: () => void
  uri: string
}

const EMPTY_VERSION = '__empty_version__'

function versionLabel(commit: SnapshotCommit): string {
  const date = commit.author?.time_seconds
    ? ` · ${formatDateTime(new Date(commit.author.time_seconds * 1000))}`
    : ''
  return `${commit.message} · ${commit.oid.slice(0, 12)}${date}`
}

export function SnapshotComparisonDialog({ onClose, uri }: Props) {
  const { t } = useTranslation('dataManagement')
  const source = useMemo(createDataManagementClient, [])
  const controller = useRef(new AbortController())
  const [commits, setCommits] = useState<SnapshotCommit[] | null>(null)
  const [base, setBase] = useState('')
  const [target, setTarget] = useState('')
  const [comparison, setComparison] = useState<SnapshotComparison | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const versionOptions = useMemo(
    () =>
      commits?.map((commit) => ({
        value: commit.oid,
        label: versionLabel(commit),
      })) ?? [],
    [commits],
  )

  useEffect(() => {
    const signal = controller.current.signal
    void listSnapshots(source, uri, signal, 500)
      .then((items) => {
        if (signal.aborted) return
        setCommits(items)
        setTarget('current')
        setBase(items[0]?.oid ?? '')
      })
      .catch((cause: unknown) => {
        if (!signal.aborted)
          setError(cause instanceof Error ? cause.message : String(cause))
      })
    return () => controller.current.abort()
  }, [source, uri])

  useEffect(() => {
    setComparison(null)
    setError('')
    if (!commits || !target || base === target) {
      setBusy(false)
      return
    }

    const request = new AbortController()
    setBusy(true)
    void (
      target === 'current'
        ? diffSnapshotWithCurrent(source, uri, base, request.signal)
        : diffSnapshot(source, uri, target, base || undefined, request.signal)
    )
      .then((result) => {
        if (!request.signal.aborted) setComparison(result)
      })
      .catch((cause: unknown) => {
        if (!request.signal.aborted)
          setError(cause instanceof Error ? cause.message : String(cause))
      })
      .finally(() => {
        if (!request.signal.aborted) setBusy(false)
      })

    return () => request.abort()
  }, [base, commits, source, target, uri])

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="flex h-[min(90dvh,760px)] min-w-0 flex-col overflow-hidden sm:max-w-5xl">
        <DialogHeader className="shrink-0">
          <DialogTitle>{t('diffComparison')}</DialogTitle>
          <DialogDescription className="min-w-0 break-all">
            {uri}
          </DialogDescription>
        </DialogHeader>

        {commits === null && !error ? (
          <p className="text-sm text-muted-foreground">{t('loading')}</p>
        ) : commits?.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t('noSnapshots')}</p>
        ) : commits ? (
          <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-4">
            <div className="grid shrink-0 gap-3 sm:grid-cols-2">
              <div className="min-w-0 space-y-1.5 text-sm font-medium">
                <label htmlFor="snapshot-base-version">
                  {t('previousVersion')}
                </label>
                <Select
                  value={base || EMPTY_VERSION}
                  items={[
                    { value: EMPTY_VERSION, label: t('emptyVersion') },
                    ...versionOptions,
                  ]}
                  onValueChange={(value) =>
                    setBase(value === EMPTY_VERSION ? '' : value)
                  }
                >
                  <SelectTrigger
                    id="snapshot-base-version"
                    className="w-full min-w-0 bg-background"
                  >
                    <SelectValue className="min-w-0 truncate" />
                  </SelectTrigger>
                  <SelectContent
                    align="start"
                    className="w-[var(--anchor-width)] max-w-[calc(100vw-3rem)]"
                  >
                    <SelectItem value={EMPTY_VERSION}>
                      {t('emptyVersion')}
                    </SelectItem>
                    {commits.map((commit) => (
                      <SelectItem key={commit.oid} value={commit.oid}>
                        {versionLabel(commit)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="min-w-0 space-y-1.5 text-sm font-medium">
                <label htmlFor="snapshot-target-version">
                  {t('selectedVersion')}
                </label>
                <Select
                  value={target}
                  items={[
                    { value: 'current', label: t('currentContent') },
                    ...versionOptions,
                  ]}
                  onValueChange={setTarget}
                >
                  <SelectTrigger
                    id="snapshot-target-version"
                    className="w-full min-w-0 bg-background"
                  >
                    <SelectValue className="min-w-0 truncate" />
                  </SelectTrigger>
                  <SelectContent
                    align="start"
                    className="w-[var(--anchor-width)] max-w-[calc(100vw-3rem)]"
                  >
                    <SelectItem value="current">
                      {t('currentContent')}
                    </SelectItem>
                    {commits.map((commit) => (
                      <SelectItem key={commit.oid} value={commit.oid}>
                        {versionLabel(commit)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div
              className="h-5 shrink-0 text-sm text-muted-foreground"
              aria-live="polite"
            >
              {busy
                ? t('comparingVersions')
                : base === target
                  ? t('selectDifferentVersions')
                  : null}
            </div>
            <div className="min-h-0 flex-1 overflow-hidden">
              {comparison !== null && <SnapshotDiffViewer {...comparison} />}
            </div>
          </div>
        ) : null}
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
      </DialogContent>
    </Dialog>
  )
}
