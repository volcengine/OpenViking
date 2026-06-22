import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties, PointerEvent as ReactPointerEvent } from 'react'
import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import {
  ArrowLeftIcon,
  BotIcon,
  ClipboardIcon,
  TerminalIcon,
} from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '#/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '#/components/ui/dialog'
import { FilePreview } from '#/routes/resources/-components/file-preview'
import { AddResourceForm } from '#/routes/resources/-components/add-resource-page'
import { UploadTaskDialog } from '#/routes/resources/-components/upload-task-dialog'
import {
  ResourceUploadProvider,
  useResourceUpload,
} from '#/routes/resources/-hooks/use-resource-upload'
import { FindPalette } from '#/routes/resources/-components/find-palette'
import { copyTextToClipboard } from '#/lib/clipboard'
import {
  useInvalidateVikingFs,
  useVikingFsList,
} from '#/routes/resources/-hooks/viking-fm'
import { fetchFsStat } from '#/routes/resources/-lib/api'
import {
  fileNameFromUri,
  normalizeDirUri,
  normalizeFileUri,
  parentUri,
} from '#/routes/resources/-lib/normalize'
import type { VikingFsEntry } from '#/routes/resources/-types/viking-fm'

import { AgentPanel } from './-components/agent-panel'
import {
  ContextExplorerHeader,
  ContextTree,
  PanelTab,
  PlaygroundResizeHandle,
} from './-components/context-explorer'
import { TerminalPanel } from './-components/terminal-panel'
import {
  ROOT_URI,
  PLAYGROUND_LEFT_WIDTH,
  PLAYGROUND_LEFT_WIDTH_STORAGE_KEY,
  PLAYGROUND_MAIN_MIN_WIDTH,
  PLAYGROUND_RIGHT_WIDTH,
  PLAYGROUND_RIGHT_WIDTH_STORAGE_KEY,
} from './-lib/constants'
import type {
  PlaygroundPanel,
  PlaygroundSearch,
  ResourceOpenHandler,
} from './-lib/types'
import {
  clampNumber,
  cleanVikingUri,
  createEntryFromUri,
  getErrorMessage,
  getAncestorUris,
  isDirectoryLevelFile,
  mergeExpanded,
  normalizePlaygroundResourceUri,
  readStoredNumber,
  visibleContextEntries,
} from './-lib/utils'

export const Route = createFileRoute('/playground')({
  validateSearch: (search: Record<string, unknown>): PlaygroundSearch => ({
    file: typeof search.file === 'string' ? search.file : undefined,
    panel:
      search.panel === 'agent' || search.panel === 'terminal'
        ? search.panel
        : undefined,
    session: typeof search.session === 'string' ? search.session : undefined,
    upload: search.upload === true || search.upload === 'true',
    uri: typeof search.uri === 'string' ? search.uri : undefined,
  }),
  component: PlaygroundRoute,
})

function PlaygroundRoute() {
  return (
    <ResourceUploadProvider>
      <PlaygroundWorkbench />
    </ResourceUploadProvider>
  )
}

function PlaygroundWorkbench() {
  const { t } = useTranslation(['playground', 'resources'])
  const search = Route.useSearch()
  const navigate = useNavigate({ from: Route.fullPath })
  const initialCurrentUri = useMemo(
    () =>
      search.file
        ? normalizeDirUri(parentUri(search.file))
        : normalizeDirUri(search.uri || ROOT_URI),
    [search.file, search.uri],
  )

  const [currentUri, setCurrentUri] = useState(initialCurrentUri)
  const [selectedFile, setSelectedFile] = useState<VikingFsEntry | null>(() =>
    search.file && !isDirectoryLevelFile(search.file)
      ? createEntryFromUri(search.file, false)
      : createEntryFromUri(initialCurrentUri, true),
  )
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(
    () => new Set(getAncestorUris(initialCurrentUri)),
  )
  const [activePanel, setActivePanel] = useState<PlaygroundPanel>(
    search.panel ?? 'agent',
  )
  const [actionPanelOpen, setActionPanelOpen] = useState(false)
  const isCompactLayout = useIsCompactPlaygroundLayout()
  const [uploadDialogOpen, setUploadDialogOpen] = useState(
    () => search.upload ?? false,
  )
  const [findPaletteOpen, setFindPaletteOpen] = useState(false)
  const [taskDialogOpen, setTaskDialogOpen] = useState(false)
  const [openingUri, setOpeningUri] = useState<string | null>(null)
  const layoutRef = useRef<HTMLDivElement>(null)
  const [leftWidth, setLeftWidth] = useState(() =>
    readStoredNumber(
      PLAYGROUND_LEFT_WIDTH_STORAGE_KEY,
      PLAYGROUND_LEFT_WIDTH.default,
      PLAYGROUND_LEFT_WIDTH.min,
      PLAYGROUND_LEFT_WIDTH.max,
    ),
  )
  const [rightWidth, setRightWidth] = useState(() =>
    readStoredNumber(
      PLAYGROUND_RIGHT_WIDTH_STORAGE_KEY,
      PLAYGROUND_RIGHT_WIDTH.default,
      PLAYGROUND_RIGHT_WIDTH.min,
      PLAYGROUND_RIGHT_WIDTH.max,
    ),
  )
  const [resizingPane, setResizingPane] = useState<'context' | 'action' | null>(
    null,
  )
  const isDraggingPaneRef = useRef(false)
  const activeResizeTeardownRef = useRef<(() => void) | null>(null)
  const leftWidthRef = useRef(leftWidth)
  const rightWidthRef = useRef(rightWidth)
  leftWidthRef.current = leftWidth
  rightWidthRef.current = rightWidth

  const listQuery = useVikingFsList(currentUri, {
    output: 'agent',
    showAllHidden: true,
    nodeLimit: 500,
  })
  const {
    activeTaskCount,
    hasActiveTasks,
    isRefreshingTasks,
    refreshTasks,
    tasks,
  } = useResourceUpload()
  const { invalidateList } = useInvalidateVikingFs()

  const syncSearch = useCallback(
    (next: {
      file?: string
      panel?: PlaygroundPanel
      session?: string
      upload?: boolean
      uri?: string
    }) => {
      navigate({
        replace: true,
        search: (prev) => {
          const merged: Record<string, unknown> = { ...prev, ...next }
          return Object.fromEntries(
            Object.entries(merged).filter(([, value]) => value !== undefined),
          )
        },
      })
    },
    [navigate],
  )

  useEffect(() => {
    const normalized = search.file
      ? normalizeDirUri(parentUri(search.file))
      : normalizeDirUri(search.uri || ROOT_URI)
    setCurrentUri(normalized)
    setSelectedFile(
      search.file && !isDirectoryLevelFile(search.file)
        ? createEntryFromUri(search.file, false)
        : createEntryFromUri(normalized, true),
    )
    setExpandedKeys((prev) => mergeExpanded(prev, getAncestorUris(normalized)))
  }, [search.file, search.uri])

  useEffect(() => {
    if (search.panel === 'agent' || search.panel === 'terminal') {
      setActivePanel(search.panel)
    }
  }, [search.panel])

  useEffect(() => {
    if (search.upload) {
      setUploadDialogOpen(true)
    }
  }, [search.upload])

  const handleUploadDialogOpenChange = useCallback(
    (open: boolean) => {
      setUploadDialogOpen(open)
      if (!open && search.upload) {
        syncSearch({ upload: undefined })
      }
    },
    [search.upload, syncSearch],
  )

  const revealResource = useCallback(
    async (rawUri: string) => {
      const cleaned = cleanVikingUri(rawUri)
      if (!cleaned) return

      setOpeningUri(cleaned)
      const targetUri = normalizePlaygroundResourceUri(cleaned)
      try {
        const stat = await fetchFsStat(targetUri, { throwOnError: true })
        const isDir = stat.isDir || targetUri.endsWith('/')
        const normalized = isDir
          ? normalizeDirUri(targetUri)
          : normalizeFileUri(targetUri)
        const nextCurrentUri = isDir
          ? normalizeDirUri(normalized)
          : normalizeDirUri(parentUri(normalized))

        setCurrentUri(nextCurrentUri)
        setSelectedFile({
          ...stat,
          isDir,
          name: stat.name || fileNameFromUri(normalized),
          uri: normalized,
        })
        setExpandedKeys((prev) =>
          mergeExpanded(prev, getAncestorUris(nextCurrentUri)),
        )
        syncSearch({
          file: isDir ? undefined : normalized,
          uri: nextCurrentUri,
        })
      } catch (error) {
        const fallbackIsDir = targetUri.endsWith('/')
        const normalized = fallbackIsDir
          ? normalizeDirUri(targetUri)
          : normalizeFileUri(targetUri)
        const nextCurrentUri = fallbackIsDir
          ? normalized
          : normalizeDirUri(parentUri(normalized))

        setCurrentUri(nextCurrentUri)
        setSelectedFile(null)
        setExpandedKeys((prev) =>
          mergeExpanded(prev, getAncestorUris(nextCurrentUri)),
        )
        syncSearch({
          file: undefined,
          uri: nextCurrentUri,
        })
        toast.error(getErrorMessage(error) || t('readFailed', { uri: cleaned }))
      } finally {
        setOpeningUri(null)
      }
    },
    [syncSearch, t],
  )

  const handleSelectDirectory = useCallback(
    (entry: VikingFsEntry) => {
      const normalized = normalizeDirUri(entry.uri)
      setCurrentUri(normalized)
      setSelectedFile({ ...entry, isDir: true, uri: normalized })
      setExpandedKeys((prev) =>
        mergeExpanded(prev, getAncestorUris(normalized)),
      )
      syncSearch({ file: undefined, uri: normalized })
    },
    [syncSearch],
  )

  const handleSelectFile = useCallback(
    (entry: VikingFsEntry) => {
      const normalized = normalizeFileUri(entry.uri)
      if (isDirectoryLevelFile(normalized)) {
        const dirUri = normalizeDirUri(parentUri(normalized))
        setCurrentUri(dirUri)
        setSelectedFile(createEntryFromUri(dirUri, true))
        setExpandedKeys((prev) => mergeExpanded(prev, getAncestorUris(dirUri)))
        syncSearch({ file: undefined, uri: dirUri })
        return
      }

      const nextCurrentUri = normalizeDirUri(parentUri(normalized))
      setCurrentUri(nextCurrentUri)
      setSelectedFile({ ...entry, isDir: false, uri: normalized })
      setExpandedKeys((prev) =>
        mergeExpanded(prev, getAncestorUris(nextCurrentUri)),
      )
      syncSearch({ file: normalized, uri: nextCurrentUri })
    },
    [syncSearch],
  )

  const handlePanelChange = useCallback(
    (panel: PlaygroundPanel) => {
      setActivePanel(panel)
      syncSearch({ panel })
    },
    [syncSearch],
  )

  const handleOpenActionPanel = useCallback(
    (panel: PlaygroundPanel) => {
      handlePanelChange(panel)
      setActionPanelOpen(true)
    },
    [handlePanelChange],
  )

  const handleOpenProcessingTasks = useCallback(() => {
    setTaskDialogOpen(true)
    void refreshTasks()
  }, [refreshTasks])

  const handleOpenSearch = useCallback(() => {
    setFindPaletteOpen(true)
  }, [])

  const handleNavigateDirectory = useCallback(
    (rawUri: string) => {
      const normalized = normalizeDirUri(rawUri)
      setCurrentUri(normalized)
      setSelectedFile(createEntryFromUri(normalized, true))
      setExpandedKeys((prev) =>
        mergeExpanded(prev, getAncestorUris(normalized)),
      )
      syncSearch({ file: undefined, uri: normalized })
    },
    [syncSearch],
  )

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setFindPaletteOpen((open) => !open)
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [])

  const selectedUri = selectedFile?.uri ?? currentUri
  const displayUri =
    selectedUri === ROOT_URI ? selectedUri : selectedUri.replace(/\/$/, '')
  const entries = visibleContextEntries(listQuery.data?.entries ?? [])
  const layoutStyle = useMemo(
    () =>
      ({
        '--playground-left-width': `${leftWidth}px`,
        '--playground-right-width': `${rightWidth}px`,
      }) as CSSProperties,
    [leftWidth, rightWidth],
  )

  const handleResizeStart = useCallback(
    (pane: 'context' | 'action', event: ReactPointerEvent<HTMLDivElement>) => {
      event.preventDefault()
      event.currentTarget.setPointerCapture(event.pointerId)
      isDraggingPaneRef.current = true
      setResizingPane(pane)

      const startX = event.clientX
      const startLeftWidth = leftWidthRef.current
      const startRightWidth = rightWidthRef.current
      const layoutRect = layoutRef.current?.getBoundingClientRect()

      const getMaxWidth = (
        side: 'left' | 'right',
        currentOppositeWidth: number,
      ) => {
        if (!layoutRect) {
          return side === 'left'
            ? PLAYGROUND_LEFT_WIDTH.max
            : PLAYGROUND_RIGHT_WIDTH.max
        }

        const hardMax =
          side === 'left'
            ? PLAYGROUND_LEFT_WIDTH.max
            : PLAYGROUND_RIGHT_WIDTH.max
        const availableMax =
          layoutRect.width - currentOppositeWidth - PLAYGROUND_MAIN_MIN_WIDTH
        return Math.max(
          side === 'left'
            ? PLAYGROUND_LEFT_WIDTH.min
            : PLAYGROUND_RIGHT_WIDTH.min,
          Math.min(hardMax, availableMax),
        )
      }

      const onMove = (moveEvent: PointerEvent) => {
        const deltaX = moveEvent.clientX - startX
        if (pane === 'context') {
          const nextWidth = clampNumber(
            startLeftWidth + deltaX,
            PLAYGROUND_LEFT_WIDTH.min,
            getMaxWidth('left', rightWidthRef.current),
          )
          setLeftWidth(nextWidth)
          window.localStorage.setItem(
            PLAYGROUND_LEFT_WIDTH_STORAGE_KEY,
            String(nextWidth),
          )
          return
        }

        const nextWidth = clampNumber(
          startRightWidth - deltaX,
          PLAYGROUND_RIGHT_WIDTH.min,
          getMaxWidth('right', leftWidthRef.current),
        )
        setRightWidth(nextWidth)
        window.localStorage.setItem(
          PLAYGROUND_RIGHT_WIDTH_STORAGE_KEY,
          String(nextWidth),
        )
      }

      const onUp = () => {
        isDraggingPaneRef.current = false
        activeResizeTeardownRef.current = null
        setResizingPane(null)
        document.removeEventListener('pointermove', onMove)
        document.removeEventListener('pointerup', onUp)
        document.removeEventListener('pointercancel', onUp)
        document.body.style.cursor = ''
        document.body.style.userSelect = ''
      }

      document.body.style.cursor = 'col-resize'
      document.body.style.userSelect = 'none'
      document.addEventListener('pointermove', onMove)
      document.addEventListener('pointerup', onUp)
      document.addEventListener('pointercancel', onUp)
      activeResizeTeardownRef.current = onUp
    },
    [],
  )

  useEffect(() => {
    return () => {
      // Tear down any in-flight drag so the document listeners don't outlive
      // the component when it unmounts mid-resize.
      activeResizeTeardownRef.current?.()
    }
  }, [])

  return (
    <div className="-mx-4 -my-6 flex h-[calc(100svh-3rem)] min-h-0 flex-col bg-background md:-mx-6">
      <div
        ref={layoutRef}
        className="flex min-h-0 flex-1 flex-col bg-background lg:flex-row"
        style={layoutStyle}
      >
        <aside className="flex min-h-[180px] min-w-0 shrink-0 basis-[36%] flex-col border-b bg-muted/20 lg:min-h-0 lg:w-[var(--playground-left-width)] lg:min-w-[var(--playground-left-width)] lg:basis-auto lg:border-b-0">
          <ContextExplorerHeader
            activeTaskCount={activeTaskCount}
            hasActiveTasks={hasActiveTasks}
            hasTasks={tasks.length > 0}
            isRefreshing={listQuery.isFetching}
            isRefreshingTasks={isRefreshingTasks}
            onAddResource={() => setUploadDialogOpen(true)}
            onOpenProcessingTasks={handleOpenProcessingTasks}
            onOpenSearch={handleOpenSearch}
            onRefresh={() => {
              void invalidateList(currentUri)
              void listQuery.refetch()
            }}
          />
          <div className="min-h-0 flex-1">
            <ContextTree
              currentUri={currentUri}
              selectedFileUri={
                selectedFile && !selectedFile.isDir ? selectedFile.uri : null
              }
              expandedKeys={expandedKeys}
              onExpandedKeysChange={setExpandedKeys}
              onSelectDirectory={handleSelectDirectory}
              onSelectFile={handleSelectFile}
            />
          </div>
        </aside>
        <PlaygroundResizeHandle
          active={resizingPane === 'context'}
          label={t('resizeContext')}
          onPointerDown={(event) => handleResizeStart('context', event)}
        />

        <main className="flex min-h-0 min-w-0 flex-1 flex-col lg:border-b-0">
          <div className="flex min-h-14 items-center gap-3 border-b px-4">
            <button
              type="button"
              className="min-w-0 flex-1 truncate rounded px-1.5 py-1 text-left font-mono text-xs font-semibold text-foreground transition-colors hover:bg-muted"
              title={selectedUri}
              onClick={() => void revealResource(selectedUri)}
            >
              {displayUri}
            </button>
            <Button
              type="button"
              size="icon-sm"
              variant="ghost"
              title={t('copyUri')}
              onClick={() => {
                void copyTextToClipboard(selectedUri)
                  .then(() => {
                    toast.success(t('copied'))
                  })
                  .catch(() => {
                    toast.error(t('copyFailed'))
                  })
              }}
            >
              <ClipboardIcon className="size-4" />
            </Button>
            <div className="flex shrink-0 items-center gap-1 lg:hidden">
              <Button
                type="button"
                size="icon-sm"
                variant={activePanel === 'terminal' ? 'secondary' : 'ghost'}
                title={t('tabs.terminal')}
                aria-label={t('tabs.terminal')}
                onClick={() => handleOpenActionPanel('terminal')}
              >
                <TerminalIcon className="size-4" />
              </Button>
              <Button
                type="button"
                size="icon-sm"
                variant={activePanel === 'agent' ? 'secondary' : 'ghost'}
                title={t('tabs.agent')}
                aria-label={t('tabs.agent')}
                onClick={() => handleOpenActionPanel('agent')}
              >
                <BotIcon className="size-4" />
              </Button>
            </div>
          </div>
          <div className="min-h-0 flex-1">
            <FilePreview
              file={selectedFile}
              hideDirectoryHeader
              onClose={() => setSelectedFile(null)}
              showCloseButton={false}
            />
          </div>
        </main>
        <PlaygroundResizeHandle
          active={resizingPane === 'action'}
          label={t('resizeAction')}
          onPointerDown={(event) => handleResizeStart('action', event)}
        />

        {!isCompactLayout ? (
          <aside className="hidden min-h-0 min-w-0 flex-col bg-muted/15 lg:flex lg:w-[var(--playground-right-width)] lg:min-w-[var(--playground-right-width)]">
            <PlaygroundActionPanel
              activePanel={activePanel}
              currentUri={currentUri}
              entries={entries}
              onOpenAddResource={() => setUploadDialogOpen(true)}
              onOpenResource={revealResource}
              onPanelChange={handlePanelChange}
              onSessionChange={(sessionId) =>
                syncSearch({ session: sessionId })
              }
              openingUri={openingUri}
              sessionId={search.session}
            />
          </aside>
        ) : null}
      </div>

      {isCompactLayout ? (
        <PlaygroundMobileActionScreen
          activePanel={activePanel}
          currentUri={currentUri}
          entries={entries}
          onClose={() => setActionPanelOpen(false)}
          onOpenAddResource={() => setUploadDialogOpen(true)}
          onOpenResource={revealResource}
          onPanelChange={handlePanelChange}
          onSessionChange={(sessionId) => syncSearch({ session: sessionId })}
          open={actionPanelOpen}
          openingUri={openingUri}
          sessionId={search.session}
        />
      ) : null}

      <Dialog
        open={uploadDialogOpen}
        onOpenChange={handleUploadDialogOpenChange}
      >
        <DialogContent className="max-h-[min(86vh,760px)] gap-0 overflow-hidden p-0 sm:max-w-4xl">
          <DialogHeader className="border-b px-6 py-5">
            <DialogTitle className="text-xl">
              {t('addResource.title')}
            </DialogTitle>
            <DialogDescription>
              {t('addResource.description')}
            </DialogDescription>
          </DialogHeader>
          <div className="max-h-[calc(min(86vh,760px)-6rem)] overflow-y-auto px-6 py-5">
            <AddResourceForm
              onSubmitted={() => {
                handleUploadDialogOpenChange(false)
                void invalidateList()
                toast.success(t('addResource.submitted'))
              }}
            />
          </div>
        </DialogContent>
      </Dialog>
      <UploadTaskDialog
        open={taskDialogOpen}
        onOpenChange={setTaskDialogOpen}
        tasks={tasks}
      />
      <FindPalette
        open={findPaletteOpen}
        onClose={() => setFindPaletteOpen(false)}
        onNavigate={(uri) => void revealResource(uri)}
        onNavigateDir={handleNavigateDirectory}
        scopeUri={currentUri}
      />
    </div>
  )
}

function useIsCompactPlaygroundLayout() {
  const [isCompact, setIsCompact] = useState(() =>
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia('(max-width: 1023px)').matches
      : false,
  )

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') {
      return
    }
    const mql = window.matchMedia('(max-width: 1023px)')
    const onChange = () => setIsCompact(mql.matches)
    onChange()
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [])

  return isCompact
}

function PlaygroundActionPanel({
  activePanel,
  currentUri,
  entries,
  onOpenAddResource,
  onOpenResource,
  onPanelChange,
  onSessionChange,
  openingUri,
  sessionId,
}: {
  activePanel: PlaygroundPanel
  currentUri: string
  entries: VikingFsEntry[]
  onOpenAddResource: () => void
  onOpenResource: ResourceOpenHandler
  onPanelChange: (panel: PlaygroundPanel) => void
  onSessionChange: (sessionId: string) => void
  openingUri: string | null
  sessionId?: string
}) {
  const { t } = useTranslation('playground')

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex h-14 shrink-0 items-center border-b px-3">
        <PlaygroundActionTabs
          activePanel={activePanel}
          onPanelChange={onPanelChange}
        />
      </div>

      <PlaygroundActionContent
        activePanel={activePanel}
        currentUri={currentUri}
        entries={entries}
        onOpenAddResource={onOpenAddResource}
        onOpenResource={onOpenResource}
        onSessionChange={onSessionChange}
        openingUri={openingUri}
        sessionId={sessionId}
      />
    </div>
  )
}

function PlaygroundMobileActionScreen({
  activePanel,
  currentUri,
  entries,
  onClose,
  onOpenAddResource,
  onOpenResource,
  onPanelChange,
  onSessionChange,
  open,
  openingUri,
  sessionId,
}: {
  activePanel: PlaygroundPanel
  currentUri: string
  entries: VikingFsEntry[]
  onClose: () => void
  onOpenAddResource: () => void
  onOpenResource: ResourceOpenHandler
  onPanelChange: (panel: PlaygroundPanel) => void
  onSessionChange: (sessionId: string) => void
  open: boolean
  openingUri: string | null
  sessionId?: string
}) {
  const { t } = useTranslation(['playground', 'resources'])

  if (!open) {
    return null
  }

  return (
    <div className="fixed inset-0 z-50 flex min-h-0 flex-col bg-background lg:hidden">
      <div className="flex h-14 shrink-0 items-center gap-2 border-b bg-background px-3">
        <Button
          type="button"
          size="icon-sm"
          variant="ghost"
          title={t('dirBrowser.back', { ns: 'resources' })}
          aria-label={t('dirBrowser.back', { ns: 'resources' })}
          onClick={onClose}
        >
          <ArrowLeftIcon className="size-4" />
        </Button>
        <PlaygroundActionTabs
          activePanel={activePanel}
          onPanelChange={onPanelChange}
        />
      </div>
      <div className="min-h-0 flex-1">
        <PlaygroundActionContent
          activePanel={activePanel}
          currentUri={currentUri}
          entries={entries}
          onOpenAddResource={onOpenAddResource}
          onOpenResource={onOpenResource}
          onSessionChange={onSessionChange}
          openingUri={openingUri}
          sessionId={sessionId}
        />
      </div>
    </div>
  )
}

function PlaygroundActionTabs({
  activePanel,
  onPanelChange,
}: {
  activePanel: PlaygroundPanel
  onPanelChange: (panel: PlaygroundPanel) => void
}) {
  const { t } = useTranslation('playground')

  return (
    <div className="inline-flex rounded-lg border bg-background p-1">
      <PanelTab
        active={activePanel === 'terminal'}
        icon={TerminalIcon}
        label={t('tabs.terminal')}
        onClick={() => onPanelChange('terminal')}
      />
      <PanelTab
        active={activePanel === 'agent'}
        icon={BotIcon}
        label={t('tabs.agent')}
        onClick={() => onPanelChange('agent')}
      />
    </div>
  )
}

function PlaygroundActionContent({
  activePanel,
  currentUri,
  entries,
  onOpenAddResource,
  onOpenResource,
  onSessionChange,
  openingUri,
  sessionId,
}: {
  activePanel: PlaygroundPanel
  currentUri: string
  entries: VikingFsEntry[]
  onOpenAddResource: () => void
  onOpenResource: ResourceOpenHandler
  onSessionChange: (sessionId: string) => void
  openingUri: string | null
  sessionId?: string
}) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      {activePanel === 'terminal' ? (
        <TerminalPanel
          currentUri={currentUri}
          entries={entries}
          onOpenAddResource={onOpenAddResource}
          onOpenResource={onOpenResource}
          onSessionChange={onSessionChange}
          openingUri={openingUri}
          sessionId={sessionId}
        />
      ) : (
        <AgentPanel
          initialSessionId={sessionId}
          onOpenResource={onOpenResource}
          onSessionChange={onSessionChange}
        />
      )}
    </div>
  )
}
