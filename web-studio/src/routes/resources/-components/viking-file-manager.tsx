import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ChevronRight,
  FolderOpen,
  RefreshCcw,
  Search,
  Upload,
} from 'lucide-react'
import { toast } from 'sonner'
import { useTranslation } from 'react-i18next'

import { Button } from '#/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '#/components/ui/dialog'

import { normalizeDirUri, normalizeFileUri, parentUri } from '../-lib/normalize'
import { useResourceUpload } from '../-hooks/use-resource-upload'
import { useInvalidateVikingFs, useVikingFsList } from '../-hooks/viking-fm'
import type { VikingFsEntry } from '../-types/viking-fm'
import { AddResourceForm } from './add-resource-page'
import { FileList } from './file-list'
import { FilePreview } from './file-preview'
import { FileTree } from './file-tree'
import { FindPalette } from './find-palette'
import { UploadTaskDialog } from './upload-task-dialog'

interface VikingFileManagerProps {
  initialUri?: string
  initialFile?: string
  initialUploadOpen?: boolean
  onUriChange?: (uri: string) => void
}

function getAncestorUris(uri: string): Array<string> {
  const normalized = normalizeDirUri(uri)
  if (normalized === 'viking://') {
    return ['viking://']
  }

  const body = normalized.slice('viking://'.length, -1)
  const parts = body.split('/').filter(Boolean)

  const ancestors = ['viking://']
  let running = 'viking://'
  for (const part of parts) {
    running = `${running}${part}/`
    ancestors.push(running)
  }

  return ancestors
}

function isDirectoryUri(uri: string): boolean {
  return uri.endsWith('/')
}

export function VikingFileManager({
  initialUri,
  initialFile,
  initialUploadOpen,
  onUriChange,
}: VikingFileManagerProps) {
  const { t } = useTranslation('resources')
  const { tasks, hasActiveTasks } = useResourceUpload()
  const [currentUri, setCurrentUri] = useState(
    normalizeDirUri(initialUri || 'viking://'),
  )
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(
    new Set(['viking://']),
  )
  const [selectedFile, setSelectedFile] = useState<VikingFsEntry | null>(null)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [uploadDialogOpen, setUploadDialogOpen] = useState(
    initialUploadOpen ?? false,
  )
  const [taskDialogOpen, setTaskDialogOpen] = useState(false)

  useEffect(() => {
    const normalized = normalizeDirUri(initialUri || 'viking://')
    setCurrentUri(normalized)
    setExpandedKeys((prev) => {
      const next = new Set(prev)
      for (const ancestor of getAncestorUris(normalized)) {
        next.add(ancestor)
      }
      return next
    })
  }, [initialUri])

  useEffect(() => {
    if (!initialFile) return
    const fileUri = normalizeFileUri(initialFile)
    const name = fileUri.split('/').pop() || fileUri
    setSelectedFile({
      uri: fileUri,
      name,
      isDir: false,
      size: '',
      sizeBytes: null,
      modTime: '',
      modTimestamp: null,
      abstract: '',
    })
  }, [initialFile])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        setPaletteOpen((prev) => !prev)
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [])

  const updateUri = useCallback((uri: string) => {
    const normalized = normalizeDirUri(uri)
    setCurrentUri(normalized)
    setSelectedFile(null)
  }, [])

  const handleNavigateFromSearch = useCallback((uri: string) => {
    if (isDirectoryUri(uri)) {
      const normalized = normalizeDirUri(uri)
      setCurrentUri(normalized)
      setSelectedFile(null)
    } else {
      const dirUri = parentUri(uri)
      setCurrentUri(normalizeDirUri(dirUri))
      const name = uri.split('/').pop() || uri
      setSelectedFile({
        uri: normalizeFileUri(uri),
        name,
        isDir: false,
        size: '',
        sizeBytes: null,
        modTime: '',
        modTimestamp: null,
        abstract: '',
      })
    }
  }, [])

  const listQuery = useVikingFsList(currentUri, {
    output: 'agent',
    showAllHidden: true,
    nodeLimit: 500,
  })
  const { invalidateList } = useInvalidateVikingFs()
  const completedTaskIdsRef = useRef<Set<string>>(new Set())
  const processingNoticeShownRef = useRef(false)
  const processingToastIdRef = useRef<string | number | null>(null)

  const entries = useMemo(
    () => listQuery.data?.entries || [],
    [listQuery.data?.entries],
  )

  useEffect(() => {
    const nextCompletedIds = new Set(completedTaskIdsRef.current)
    let hasNewSuccess = false

    for (const task of tasks) {
      if (task.status === 'success' && !nextCompletedIds.has(task.id)) {
        nextCompletedIds.add(task.id)
        hasNewSuccess = true
      }
    }

    completedTaskIdsRef.current = nextCompletedIds

    if (hasNewSuccess) {
      void invalidateList()
    }
  }, [invalidateList, tasks])

  useEffect(() => {
    if (!hasActiveTasks) {
      processingNoticeShownRef.current = false
      if (processingToastIdRef.current !== null) {
        toast.dismiss(processingToastIdRef.current)
        processingToastIdRef.current = null
      }
      return
    }

    if (processingNoticeShownRef.current) {
      return
    }

    processingNoticeShownRef.current = true
    processingToastIdRef.current = toast(
      <div className="max-w-[min(94vw,720px)] text-sm leading-5">
        <span>{`${t('processingNotice.prefix')} `}</span>
        <button
          type="button"
          className="inline p-0 font-medium text-foreground underline underline-offset-4 transition-colors hover:text-primary"
          onClick={() => setTaskDialogOpen(true)}
        >
          {t('processingNotice.action')}
        </button>
        <span>{` ${t('processingNotice.suffix')}`}</span>
      </div>,
      {
        position: 'top-center',
        duration: 2500,
        className: 'w-auto max-w-[min(94vw,720px)]',
        onAutoClose: () => {
          processingToastIdRef.current = null
        },
        onDismiss: () => {
          processingToastIdRef.current = null
        },
      },
    )
  }, [hasActiveTasks])

  useEffect(
    () => () => {
      if (processingToastIdRef.current !== null) {
        toast.dismiss(processingToastIdRef.current)
      }
    },
    [],
  )

  const handleRefresh = async () => {
    await invalidateList()
    await listQuery.refetch()
  }

  const handleUploadSubmitted = () => {
    setUploadDialogOpen(false)
  }

  useEffect(() => {
    onUriChange?.(currentUri)
  }, [currentUri, onUriChange])

  const breadcrumbs = useMemo(() => {
    const body = currentUri.slice('viking://'.length).replace(/\/$/, '')
    const parts = body ? body.split('/').filter(Boolean) : []
    const crumbs: Array<{ label: string; uri: string }> = [
      { label: 'viking://', uri: 'viking://' },
    ]
    let running = 'viking://'
    for (const part of parts) {
      running = `${running}${part}/`
      crumbs.push({ label: part, uri: running })
    }
    return crumbs
  }, [currentUri])

  const showTree = currentUri !== 'viking://' || selectedFile !== null
  const showPreview = selectedFile !== null

  const [treeWidth, setTreeWidth] = useState(280)
  const dragging = useRef(false)
  const treeWidthRef = useRef(treeWidth)
  treeWidthRef.current = treeWidth

  const handleResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    dragging.current = true
    const startX = e.clientX
    const startWidth = treeWidthRef.current

    const onMove = (ev: MouseEvent) => {
      const newWidth = Math.min(
        Math.max(startWidth + ev.clientX - startX, 160),
        600,
      )
      setTreeWidth(newWidth)
    }
    const onUp = () => {
      dragging.current = false
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }, [])

  const toolbar = (
    <div className="flex h-10 items-center gap-1 border-b px-3">
      {!showTree && (
        <>
          <Button
            variant="ghost"
            size="icon"
            className="size-7"
            title={t('toolbar.refresh')}
            onClick={() => void handleRefresh()}
          >
            <RefreshCcw className="size-4" />
          </Button>
          <Button
            variant="secondary"
            size="icon"
            className="h-8 w-fit px-2"
            title={t('toolbar.search')}
            onClick={() => setPaletteOpen(true)}
          >
            <Search className="size-4" />
          </Button>
          <div className="mx-1 h-4 w-px bg-border" />
        </>
      )}
      <nav className="flex items-center gap-0.5 overflow-hidden text-sm text-muted-foreground">
        {breadcrumbs.map((crumb, i) => (
          <span key={crumb.uri} className="flex shrink-0 items-center gap-0.5">
            {i > 0 && <ChevronRight className="size-3" />}
            <button
              type="button"
              className={`rounded px-1 py-0.5 hover:bg-muted ${i === breadcrumbs.length - 1 ? 'font-medium text-foreground' : ''}`}
              onClick={() => updateUri(crumb.uri)}
            >
              {crumb.label}
            </button>
          </span>
        ))}
      </nav>
      {!showTree ? (
        <div className="ml-auto flex items-center gap-2">
          <Button
            type="button"
            size="sm"
            variant="secondary"
            className="h-8"
            onClick={() => setTaskDialogOpen(true)}
          >
            {t('toolbar.processingTasks')}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            className="h-8 gap-1.5"
            onClick={() => setUploadDialogOpen(true)}
          >
            <Upload className="size-4" />
            {t('toolbar.upload')}
          </Button>
        </div>
      ) : null}
    </div>
  )

  return (
    <div className="-mx-4 -mt-6 -mb-4 flex h-[calc(100vh-3.5rem)] flex-col md:-mx-6">
      <div className="flex min-h-0 flex-1">
        {showTree && (
          <>
            <section
              className="flex min-h-0 flex-col bg-muted/30"
              style={{ width: treeWidth, minWidth: treeWidth }}
            >
              <div className="flex h-10 items-center gap-1 overflow-hidden border-b px-2">
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-7"
                  title={t('toolbar.refresh')}
                  onClick={() => void handleRefresh()}
                >
                  <RefreshCcw className="size-4" />
                </Button>
                <div className="ml-auto flex w-fit items-center gap-1.5">
                  <Button
                    type="button"
                    size="sm"
                    variant="secondary"
                    className="h-8"
                    onClick={() => setTaskDialogOpen(true)}
                  >
                    {t('toolbar.processingTasks')}
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="secondary"
                    className="h-8 gap-1.5"
                    onClick={() => setUploadDialogOpen(true)}
                  >
                    <Upload className="size-4" />
                    {t('toolbar.upload')}
                  </Button>
                  <Button
                    variant="secondary"
                    size="icon"
                    className="h-8 w-fit px-2"
                    title={t('toolbar.search')}
                    onClick={() => setPaletteOpen(true)}
                  >
                    <Search className="size-4" />
                  </Button>
                </div>
              </div>
              <div className="min-h-0 flex-1">
                <FileTree
                  currentUri={currentUri}
                  selectedFileUri={selectedFile?.uri ?? null}
                  expandedKeys={expandedKeys}
                  onExpandedKeysChange={setExpandedKeys}
                  onSelectDirectory={updateUri}
                  onSelectFile={setSelectedFile}
                />
              </div>
            </section>
            <div
              className="w-1 shrink-0 cursor-col-resize bg-transparent transition-colors hover:bg-primary/20 active:bg-primary/30"
              onMouseDown={handleResizeStart}
            />
          </>
        )}

        {showPreview ? (
          <section className="relative flex min-h-0 min-w-0 flex-1 flex-col">
            {toolbar}
            <div className="mx-auto min-h-0 w-full max-w-5xl flex-1">
              <FilePreview
                file={selectedFile}
                onClose={() => setSelectedFile(null)}
                showCloseButton={false}
              />
            </div>
          </section>
        ) : (
          <section className="relative flex min-h-0 min-w-0 flex-1 flex-col">
            {toolbar}
            <div className="min-h-0 flex-1 overflow-auto">
              {entries.length === 0 && !listQuery.isLoading ? (
                <div className="flex h-full flex-col items-center justify-center gap-4 p-8 text-center">
                  <FolderOpen className="size-16 text-muted-foreground/20" />
                  <p className="text-sm text-muted-foreground">
                    {t('emptyState.title')}
                  </p>
                  <Button
                    size="sm"
                    variant="secondary"
                    className="gap-1.5"
                    onClick={() => setUploadDialogOpen(true)}
                  >
                    <Upload className="size-4" />
                    {t('emptyState.upload')}
                  </Button>
                </div>
              ) : (
                <FileList
                  entries={entries}
                  selectedFileUri={null}
                  onOpenDirectory={updateUri}
                  onOpenFile={(file) => setSelectedFile(file)}
                />
              )}
            </div>
          </section>
        )}
      </div>

      <FindPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        onNavigate={handleNavigateFromSearch}
        onNavigateDir={(uri) => {
          updateUri(uri)
          setPaletteOpen(false)
        }}
        scopeUri={currentUri}
      />

      <Dialog open={uploadDialogOpen} onOpenChange={setUploadDialogOpen}>
        <DialogContent className="max-h-[min(86vh,760px)] gap-0 overflow-hidden p-0 sm:max-w-4xl">
          <DialogHeader className="border-b px-6 py-5">
            <DialogTitle className="text-xl">
              {t('uploadDialog.title')}
            </DialogTitle>
            <DialogDescription>
              {t('uploadDialog.description')}
            </DialogDescription>
          </DialogHeader>
          <div className="max-h-[calc(min(86vh,760px)-6rem)] overflow-y-auto px-6 py-5">
            <AddResourceForm onSubmitted={handleUploadSubmitted} />
          </div>
        </DialogContent>
      </Dialog>

      <UploadTaskDialog
        open={taskDialogOpen}
        onOpenChange={setTaskDialogOpen}
        tasks={tasks}
      />
    </div>
  )
}
