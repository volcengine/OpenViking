import { Loader2Icon, UploadIcon } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { formatDateTime } from '#/i18n/format'
import { Button } from '#/shared/ui/button'
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from '#/shared/ui/tabs'

import type { SnapshotCommit } from '#/features/workbench/filesystem/lib/data-management'
import {
  commitSnapshot,
  createDataManagementClient,
  exportOvpack,
  importOvpack,
  listSnapshots,
} from '#/features/workbench/filesystem/lib/data-management'

interface Props {
  mode: 'history' | 'transfer'
  initialTransferTab?: 'import' | 'export'
  allowImport?: boolean
  onClose: () => void
  onCompare: () => void
  onChanged: () => void
  uri: string
  isDirectory: boolean
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function downloadBlob(blob: Blob, uri: string): void {
  const name = uri.replace(/\/$/, '').split('/').at(-1) || 'openviking'
  const safeName = name.replace(/[^\w.-]+/g, '_')
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${safeName}.ovpack`
  document.body.append(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

export function DataManagementDialog({
  mode,
  initialTransferTab = 'export',
  allowImport = true,
  onClose,
  onCompare,
  onChanged,
  uri,
  isDirectory,
}: Props) {
  const { t } = useTranslation('dataManagement')
  const source = useMemo(createDataManagementClient, [])
  const controller = useRef(new AbortController())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [historyError, setHistoryError] = useState<string | null>(null)
  const [commits, setCommits] = useState<SnapshotCommit[] | null>(null)
  const historyLoading = commits === null && historyError === null
  const [message, setMessage] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [transferTab, setTransferTab] = useState(initialTransferTab)
  const [conflict, setConflict] = useState<'fail' | 'skip' | 'overwrite'>(
    'fail',
  )

  const loadHistory = useCallback(
    async (signal: AbortSignal) => {
      setHistoryError(null)
      try {
        const items = await listSnapshots(source, uri, signal)
        if (!signal.aborted) setCommits(items)
      } catch (cause) {
        if (signal.aborted) return
        setHistoryError(errorMessage(cause))
      }
    },
    [source, uri],
  )

  useEffect(() => {
    const request = new AbortController()
    controller.current = request
    if (mode === 'history') {
      setCommits(null)
      void loadHistory(request.signal)
    }
    return () => request.abort()
  }, [mode, loadHistory])

  async function run(action: (signal: AbortSignal) => Promise<void>) {
    const signal = controller.current.signal
    setBusy(true)
    setError('')
    try {
      await action(signal)
    } catch (cause) {
      if (!signal.aborted) setError(errorMessage(cause))
    } finally {
      if (!signal.aborted) setBusy(false)
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[90dvh] min-w-0 overflow-x-hidden overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>
            {t(mode === 'history' ? 'historyTitle' : 'transferTitle')}
          </DialogTitle>
          <DialogDescription className="break-all">{uri}</DialogDescription>
        </DialogHeader>

        {mode === 'history' ? (
          <div className="min-w-0 space-y-5">
            <form
              className="flex gap-2"
              onSubmit={(event) => {
                event.preventDefault()
                if (!message.trim() || busy || historyLoading) return
                void run(async (signal) => {
                  await commitSnapshot(source, uri, message.trim(), signal)
                  if (signal.aborted) return
                  setMessage('')
                  onChanged()
                  toast.success(t('snapshotSaved'))
                  await loadHistory(signal)
                })
              }}
            >
              <input
                aria-label={t('message')}
                className="min-w-0 flex-1 rounded-md border bg-background px-3 py-2 text-sm"
                maxLength={200}
                onChange={(event) => setMessage(event.target.value)}
                placeholder={t('message')}
                value={message}
              />
              <Button
                disabled={busy || historyLoading || !message.trim()}
                type="submit"
              >
                {t('saveSnapshot')}
              </Button>
            </form>
            <div className="text-xs text-muted-foreground">
              {t('snapshotScope')}
            </div>
            {commits === null ? (
              historyError !== null ? null : (
                <p role="status">{t('loading')}</p>
              )
            ) : commits.length === 0 ? (
              historyError === null && (
                <p className="text-sm text-muted-foreground">
                  {t('noSnapshots')}
                </p>
              )
            ) : (
              <ul className="max-h-56 space-y-2 overflow-y-auto">
                {commits.map((commit) => (
                  <li
                    className="flex items-center justify-between gap-2 rounded-md border p-3"
                    key={commit.oid}
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">
                        {commit.message}
                      </p>
                      <p className="font-mono text-xs text-muted-foreground">
                        {commit.oid.slice(0, 12)}
                        {commit.author?.time_seconds
                          ? ` · ${formatDateTime(new Date(commit.author.time_seconds * 1000))}`
                          : ''}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            )}
            {historyError !== null && (
              <p role="alert" className="break-words text-sm text-destructive">
                {historyError.includes('snapshot log scan limit exceeded:')
                  ? t('historyScanLimit')
                  : historyError || t('historyLoadFailed')}
              </p>
            )}
            {!isDirectory && commits && commits.length > 0 && (
              <div className="flex justify-end">
                <Button type="button" variant="outline" onClick={onCompare}>
                  {t('diffComparison')}
                </Button>
              </div>
            )}
          </div>
        ) : (
          <Tabs
            value={allowImport ? transferTab : 'export'}
            onValueChange={(value) => {
              if (busy || (value === 'import' && !allowImport)) return
              setTransferTab(value)
              setError('')
            }}
          >
            <TabsList className="w-full" aria-label={t('transferTitle')}>
              <TabsTrigger
                value="export"
                disabled={busy && transferTab !== 'export'}
              >
                {t('exportTab')}
              </TabsTrigger>
              {allowImport && (
                <TabsTrigger
                  value="import"
                  disabled={busy && transferTab !== 'import'}
                >
                  {t('importTab')}
                </TabsTrigger>
              )}
            </TabsList>
            <TabsContent value="export" className="min-h-52 space-y-4 pt-4">
              <p className="text-xs text-muted-foreground">
                {t('exportDescription')}
              </p>
              <Button
                disabled={busy || !isDirectory}
                onClick={() =>
                  void run(async (signal) => {
                    const blob = await exportOvpack(source, uri, signal)
                    if (signal.aborted) return
                    downloadBlob(blob, uri)
                    toast.success(t('exported'))
                  })
                }
              >
                {t('exportAction')}
              </Button>
              {error && (
                <p
                  role="alert"
                  className="break-words text-sm text-destructive"
                >
                  {error}
                </p>
              )}
            </TabsContent>
            {allowImport && (
              <TabsContent
                value="import"
                keepMounted
                className="min-h-52 space-y-4 pt-4"
              >
                <p className="text-xs text-muted-foreground">
                  {t('importDescription')}
                </p>
                <div>
                  <input
                    id="ovpack-file"
                    accept=".ovpack"
                    aria-label={t('chooseFile')}
                    className="peer sr-only"
                    disabled={busy || !isDirectory}
                    onChange={(event) =>
                      setFile(event.target.files?.[0] ?? null)
                    }
                    type="file"
                  />
                  <label
                    htmlFor="ovpack-file"
                    className="flex min-h-28 cursor-pointer flex-col items-center justify-center gap-2 rounded-md border border-dashed border-input px-4 py-5 text-center transition-colors hover:border-foreground/50 hover:bg-muted/40 peer-focus-visible:outline-none peer-focus-visible:ring-2 peer-focus-visible:ring-ring peer-disabled:pointer-events-none peer-disabled:opacity-50"
                  >
                    <UploadIcon
                      aria-hidden="true"
                      className="size-5 text-muted-foreground"
                    />
                    <span className="max-w-full truncate text-sm font-medium">
                      {file?.name ?? t('chooseFile')}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {file ? t('changeFile') : t('supportedFileType')}
                    </span>
                  </label>
                </div>
                <div className="space-y-1.5 text-sm">
                  <label htmlFor="ovpack-conflict">{t('conflict')}</label>
                  <Select
                    disabled={busy}
                    items={[
                      { value: 'fail', label: t('conflictFail') },
                      { value: 'skip', label: t('conflictSkip') },
                      { value: 'overwrite', label: t('conflictOverwrite') },
                    ]}
                    onValueChange={(value) =>
                      setConflict(value as typeof conflict)
                    }
                    value={conflict}
                  >
                    <SelectTrigger
                      id="ovpack-conflict"
                      className="w-full bg-background"
                    >
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent
                      align="start"
                      className="w-[var(--anchor-width)]"
                    >
                      <SelectItem value="fail">{t('conflictFail')}</SelectItem>
                      <SelectItem value="skip">{t('conflictSkip')}</SelectItem>
                      <SelectItem value="overwrite">
                        {t('conflictOverwrite')}
                      </SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <Button
                  disabled={busy || !isDirectory || !file}
                  aria-busy={busy}
                  onClick={() =>
                    void run(async (signal) => {
                      if (!file) return
                      await importOvpack(source, uri, file, conflict, signal)
                      if (signal.aborted) return
                      onClose()
                      onChanged()
                      toast.success(t('imported'))
                    })
                  }
                >
                  {busy && (
                    <Loader2Icon aria-hidden="true" className="animate-spin" />
                  )}
                  {t(busy ? 'importing' : 'importAction')}
                </Button>
                {error && (
                  <p
                    role="alert"
                    className="break-words text-sm text-destructive"
                  >
                    {error}
                  </p>
                )}
              </TabsContent>
            )}
          </Tabs>
        )}
        {mode === 'history' && error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
      </DialogContent>
    </Dialog>
  )
}
