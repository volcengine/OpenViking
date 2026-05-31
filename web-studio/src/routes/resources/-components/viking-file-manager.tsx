import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties, PointerEvent as ReactPointerEvent } from 'react'
import {
  ArrowLeft,
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

const TREE_WIDTH_STORAGE_KEY = 'web-studio-resource-tree-width'
const TREE_HEIGHT_STORAGE_KEY = 'web-studio-resource-tree-height'

function clampNumber(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max)
}

function readStoredNumber(
  key: string,
  fallback: number,
  min: number,
  max: number,
): number {
  if (typeof window === 'undefined') {
    return fallback
  }
  const raw = window.localStorage.getItem(key)
  if (raw === null) {
    return fallback
  }

  const stored = Number(raw)
  return Number.isFinite(stored) ? clampNumber(stored, min, max) : fallback
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
    setExpandedKeys(new Set(getAncestorUris(normalized)))
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
      overview: '',
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
    setExpandedKeys(new Set(getAncestorUris(normalized)))
  }, [])

  const handleOpenDirectory = useCallback((entry: VikingFsEntry) => {
    const normalized = normalizeDirUri(entry.uri)
    setCurrentUri(normalized)
    setSelectedFile({
      ...entry,
      uri: normalized,
      isDir: true,
    })
    setExpandedKeys(new Set(getAncestorUris(normalized)))
  }, [])

  const handleNavigateFromSearch = useCallback((uri: string) => {
    if (isDirectoryUri(uri)) {
      const normalized = normalizeDirUri(uri)
      setCurrentUri(normalized)
      setSelectedFile(null)
      setExpandedKeys(new Set(getAncestorUris(normalized)))
    } else {
      const dirUri = parentUri(uri)
      const normalizedDir = normalizeDirUri(dirUri)
      setCurrentUri(normalizedDir)
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
        overview: '',
      })
      setExpandedKeys(new Set(getAncestorUris(normalizedDir)))
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

  const showPreview = selectedFile !== null || currentUri === 'viking://'

  const layoutRef = useRef<HTMLDivElement>(null)
  const [treeWidth, setTreeWidth] = useState(() =>
    readStoredNumber(TREE_WIDTH_STORAGE_KEY, 450, 180, 640),
  )
  const [treeHeight, setTreeHeight] = useState(() =>
    readStoredNumber(TREE_HEIGHT_STORAGE_KEY, 46, 34, 72),
  )
  const [isResizingTree, setResizingTree] = useState(false)
  const dragging = useRef(false)
  const treeWidthRef = useRef(treeWidth)
  const treeHeightRef = useRef(treeHeight)
  treeWidthRef.current = treeWidth
  treeHeightRef.current = treeHeight

  const treePaneStyle = useMemo(
    () =>
      ({
        '--resource-tree-height': `${treeHeight}%`,
        '--resource-tree-width': `${treeWidth}px`,
      }) as CSSProperties,
    [treeHeight, treeWidth],
  )

  const handleResizeStart = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      event.preventDefault()
      event.currentTarget.setPointerCapture(event.pointerId)
      dragging.current = true
      setResizingTree(true)

      const isDesktop = window.matchMedia('(min-width: 768px)').matches
      const startX = event.clientX
      const startY = event.clientY
      const startWidth = treeWidthRef.current
      const startHeight = treeHeightRef.current
      const layoutRect = layoutRef.current?.getBoundingClientRect()

      const onMove = (moveEvent: PointerEvent) => {
        if (isDesktop) {
          const nextWidth = clampNumber(
            startWidth + moveEvent.clientX - startX,
            180,
            640,
          )
          setTreeWidth(nextWidth)
          window.localStorage.setItem(TREE_WIDTH_STORAGE_KEY, String(nextWidth))
          return
        }

        const rect = layoutRef.current?.getBoundingClientRect() ?? layoutRect
        if (!rect) {
          const fallbackHeight = clampNumber(
            startHeight +
              ((moveEvent.clientY - startY) / Math.max(1, window.innerHeight)) *
                100,
            34,
            72,
          )
          setTreeHeight(fallbackHeight)
          window.localStorage.setItem(
            TREE_HEIGHT_STORAGE_KEY,
            String(fallbackHeight),
          )
          return
        }

        const y = moveEvent.clientY - rect.top
        const nextHeight = clampNumber(
          (y / Math.max(1, rect.height)) * 100,
          34,
          72,
        )
        setTreeHeight(nextHeight)
        window.localStorage.setItem(TREE_HEIGHT_STORAGE_KEY, String(nextHeight))
      }

      const onUp = () => {
        dragging.current = false
        setResizingTree(false)
        document.removeEventListener('pointermove', onMove)
        document.removeEventListener('pointerup', onUp)
        document.removeEventListener('pointercancel', onUp)
        document.body.style.cursor = ''
        document.body.style.userSelect = ''
      }

      document.body.style.cursor = isDesktop ? 'col-resize' : 'row-resize'
      document.body.style.userSelect = 'none'
      document.addEventListener('pointermove', onMove)
      document.addEventListener('pointerup', onUp)
      document.addEventListener('pointercancel', onUp)
    },
    [],
  )

  useEffect(() => {
    return () => {
      if (!dragging.current) return
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
  }, [])

  const isRoot = currentUri === 'viking://'

  const toolbar = (
    <div className="flex h-10 items-center gap-1 border-b px-3">
      <button
        type="button"
        disabled={isRoot}
        aria-label={t('dirBrowser.back')}
        className="inline-flex size-6 shrink-0 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
        onClick={() => updateUri(parentUri(currentUri))}
      >
        <ArrowLeft className="size-3.5" />
      </button>
      <nav className="flex flex-1 items-center gap-0.5 overflow-x-auto whitespace-nowrap text-xs text-muted-foreground md:text-sm">
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
    </div>
  )

  return (
    <div className="web-studio-resource-fs -mx-4 -my-6 flex h-[calc(100svh-3rem)] flex-col md:-mx-6">
      <div ref={layoutRef} className="flex min-h-0 flex-1 flex-col md:flex-row">
        <section
          className="flex h-[var(--resource-tree-height)] min-h-[190px] min-w-0 flex-col bg-muted/30 md:h-auto md:min-h-0 md:w-[var(--resource-tree-width)] md:min-w-[var(--resource-tree-width)]"
          style={treePaneStyle}
        >
          <div className="flex h-10 items-center gap-1 overflow-hidden border-b pl-4 pr-2 md:px-2">
            <Button
              variant="ghost"
              size="icon-sm"
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
              selectedFileUri={
                selectedFile && !selectedFile.isDir ? selectedFile.uri : null
              }
              expandedKeys={expandedKeys}
              onExpandedKeysChange={setExpandedKeys}
              onSelectDirectory={handleOpenDirectory}
              onSelectFile={setSelectedFile}
            />
          </div>
        </section>
        <div
          role="separator"
          data-dragging={isResizingTree}
          className="group flex h-2 w-full shrink-0 cursor-row-resize touch-none items-center justify-center bg-transparent transition-colors hover:bg-primary/10 active:bg-primary/20 data-[dragging=true]:bg-primary/20 md:h-auto md:w-1 md:cursor-col-resize"
          onPointerDown={handleResizeStart}
        >
          <span className="h-0.5 w-12 rounded-full bg-border transition-colors group-hover:bg-primary/50 md:h-8 md:w-0.5" />
        </div>

        {showPreview ? (
          <section className="relative flex min-h-0 min-w-0 flex-1 flex-col">
            {toolbar}
            <div className="min-h-0 w-full flex-1">
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
                  onOpenDirectory={handleOpenDirectory}
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
