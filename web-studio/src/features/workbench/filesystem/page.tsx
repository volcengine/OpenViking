import { showErrorToast } from '#/shared/lib/error-toast'
import { getRouteApi, useNavigate } from '@tanstack/react-router'
import {
  ArrowLeftIcon,
  BotIcon,
  ClipboardIcon,
  HistoryIcon,
  PackageIcon,
  PanelRightCloseIcon,
  PanelRightOpenIcon,
  TerminalIcon,
  WandSparkles,
} from 'lucide-react'
import type { CSSProperties, PointerEvent as ReactPointerEvent } from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { useStudioService } from '#/features/connection/hooks/use-studio-service'
import { AddResourceForm } from '#/features/workbench/resources/components/add-resource-page'
import { FindPalette } from '#/features/workbench/resources/components/find-palette'
import { LazyFilePreview } from '#/features/workbench/resources/components/lazy-file-preview'
import { UploadTaskDialog } from '#/features/workbench/resources/components/upload-task-dialog'
import {
  ResourceUploadProvider,
  useResourceUpload,
} from '#/features/workbench/resources/hooks/use-resource-upload'
import {
  useInvalidateVikingFs,
  useVikingFsList,
} from '#/features/workbench/resources/hooks/viking-fm'
import { fetchFsStat } from '#/features/workbench/resources/lib/api'
import {
  fileNameFromUri,
  normalizeDirUri,
  normalizeFileUri,
  parentUri,
} from '#/shared/lib/viking-fs'
import type { VikingFsEntry } from '#/shared/types/viking-fs'
import { copyTextToClipboard } from '#/shared/lib/clipboard'
import { Button } from '#/shared/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '#/shared/ui/dialog'

import { AgentPanel } from '#/features/workbench/filesystem/components/agent-panel'
import {
  ContextExplorerHeader,
  ContextTree,
  FilesystemResizeHandle,
  PanelTab,
} from '#/features/workbench/filesystem/components/context-explorer'
import { DataManagementDialog } from '#/features/workbench/filesystem/components/data-management-dialog'
import { SnapshotComparisonDialog } from '#/features/workbench/filesystem/components/snapshot-comparison-dialog'
import { TerminalPanel } from '#/features/workbench/filesystem/components/terminal-panel'
import {
  FILESYSTEM_LEFT_WIDTH,
  FILESYSTEM_LEFT_WIDTH_STORAGE_KEY,
  FILESYSTEM_MAIN_MIN_WIDTH,
  FILESYSTEM_RIGHT_COLLAPSED_STORAGE_KEY,
  FILESYSTEM_RIGHT_WIDTH,
  FILESYSTEM_RIGHT_WIDTH_STORAGE_KEY,
  ROOT_URI,
} from '#/features/workbench/filesystem/lib/constants'
import type {
  FilesystemPanel,
  ResourceOpenHandler,
} from '#/features/workbench/filesystem/lib/types'
import {
  clampNumber,
  cleanVikingUri,
  createEntryFromUri,
  getAncestorUris,
  isDirectoryLevelFile,
  mergeExpanded,
  normalizeFilesystemResourceUri,
  readFilesystemExpandedUris,
  readStoredNumber,
  visibleContextEntries,
  writeFilesystemExpandedUris,
} from '#/features/workbench/filesystem/lib/utils'

const routeApi = getRouteApi('/filesystem')

export function FilesystemRoute() {
  const { provider } = useStudioService()
  return (
    <ResourceUploadProvider enabled={provider === 'opensource'}>
      <FilesystemWorkbench />
    </ResourceUploadProvider>
  )
}

function FilesystemWorkbench() {
  const { t } = useTranslation(['filesystem', 'resources'])
  const { provider } = useStudioService()
  const canUseLocalTools = provider === 'opensource'
  const canUseCliPanel = canUseLocalTools || provider === 'volcengine'
  const { identityScopeKey } = useAppConnection()
  const search = routeApi.useSearch()
  const navigate = useNavigate({ from: '/filesystem' })
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
  const selectedFileRef = useRef(selectedFile)
  selectedFileRef.current = selectedFile
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(() =>
    mergeExpanded(
      new Set(readFilesystemExpandedUris(identityScopeKey)),
      getAncestorUris(initialCurrentUri),
    ),
  )
  const [activePanel, setActivePanel] = useState<FilesystemPanel>(
    provider === 'volcengine' ? 'terminal' : (search.panel ?? 'agent'),
  )
  const [actionPanelOpen, setActionPanelOpen] = useState(false)
  const isCompactLayout = useIsCompactFilesystemLayout()
  const [uploadDialogOpen, setUploadDialogOpen] = useState(
    () => search.upload ?? false,
  )
  const [findPaletteOpen, setFindPaletteOpen] = useState(false)
  const [taskDialogOpen, setTaskDialogOpen] = useState(false)
  const [dataManagementMode, setDataManagementMode] = useState<
    'history' | 'transfer' | 'compare' | null
  >(null)
  const [openingUri, setOpeningUri] = useState<string | null>(null)
  const layoutRef = useRef<HTMLDivElement>(null)
  const [leftWidth, setLeftWidth] = useState(() =>
    readStoredNumber(
      FILESYSTEM_LEFT_WIDTH_STORAGE_KEY,
      FILESYSTEM_LEFT_WIDTH.default,
      FILESYSTEM_LEFT_WIDTH.min,
      FILESYSTEM_LEFT_WIDTH.max,
    ),
  )
  const [rightWidth, setRightWidth] = useState(() =>
    readStoredNumber(
      FILESYSTEM_RIGHT_WIDTH_STORAGE_KEY,
      FILESYSTEM_RIGHT_WIDTH.default,
      FILESYSTEM_RIGHT_WIDTH.min,
      FILESYSTEM_RIGHT_WIDTH.max,
    ),
  )
  const [resizingPane, setResizingPane] = useState<'context' | 'action' | null>(
    null,
  )
  const [rightCollapsed, setRightCollapsed] = useState(
    () =>
      provider === 'opensource' &&
      typeof window !== 'undefined' &&
      window.localStorage.getItem(FILESYSTEM_RIGHT_COLLAPSED_STORAGE_KEY) ===
        '1',
  )
  const toggleRightCollapsed = useCallback(
    () =>
      setRightCollapsed((collapsed) => {
        window.localStorage.setItem(
          FILESYSTEM_RIGHT_COLLAPSED_STORAGE_KEY,
          collapsed ? '0' : '1',
        )
        return !collapsed
      }),
    [],
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
  const { invalidateAll, invalidateList } = useInvalidateVikingFs()

  const syncSearch = useCallback(
    (next: {
      file?: string
      panel?: FilesystemPanel
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
    setExpandedKeys(
      mergeExpanded(
        new Set(readFilesystemExpandedUris(identityScopeKey)),
        getAncestorUris(initialCurrentUri),
      ),
    )
  }, [identityScopeKey, initialCurrentUri])

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

  const handleExpandedKeysChange = useCallback(
    (next: Set<string>) => {
      setExpandedKeys(next)
      writeFilesystemExpandedUris(identityScopeKey, next)
    },
    [identityScopeKey],
  )

  useEffect(() => {
    if (provider === 'volcengine') {
      setActivePanel('terminal')
      setRightCollapsed(false)
    } else {
      setActivePanel(search.panel ?? 'agent')
    }
  }, [provider, search.panel])

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
      const targetUri = normalizeFilesystemResourceUri(cleaned)
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
        showErrorToast(error, t('readFailed', { uri: cleaned }))
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
    (panel: FilesystemPanel) => {
      if (provider === 'volcengine' && panel !== 'terminal') return
      setActivePanel(panel)
      syncSearch({ panel })
    },
    [provider, syncSearch],
  )

  const handleOpenActionPanel = useCallback(
    (panel: FilesystemPanel) => {
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

  const handleRefreshContextTree = useCallback(async () => {
    const refreshedFile = selectedFileRef.current
    await invalidateList()
    if (!refreshedFile || refreshedFile.isDir) return

    try {
      await fetchFsStat(refreshedFile.uri, { throwOnError: true })
      return
    } catch (error) {
      if ((error as { statusCode?: number }).statusCode !== 404) return
    }
    if (selectedFileRef.current?.uri !== refreshedFile.uri) return

    setSelectedFile(null)
    syncSearch({
      file: undefined,
      uri: normalizeDirUri(parentUri(refreshedFile.uri)),
    })
  }, [invalidateList, syncSearch])

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setFindPaletteOpen((open) => !open)
        return
      }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'f') {
        // Let CodeMirror keep its own in-file search when it has focus.
        if ((event.target as HTMLElement | null)?.closest('.cm-editor')) return
        event.preventDefault()
        setFindPaletteOpen(true)
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [])

  const selectedUri = selectedFile?.uri ?? currentUri
  const selectedIsDirectory = selectedFile?.isDir ?? true
  const transferUri = selectedIsDirectory
    ? selectedUri
    : normalizeDirUri(parentUri(selectedUri))
  useEffect(() => {
    setDataManagementMode(null)
  }, [selectedUri])
  const canManageSelectedUri = selectedUri !== ROOT_URI
  const canTransferSelectedUri = transferUri.startsWith('viking://resources/')
  const displayUri =
    selectedUri === ROOT_URI ? selectedUri : selectedUri.replace(/\/$/, '')
  const entries = visibleContextEntries(listQuery.data?.entries ?? [])
  const layoutStyle = useMemo(
    () =>
      ({
        '--filesystem-left-width': `${leftWidth}px`,
        '--filesystem-right-width': `${rightWidth}px`,
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
            ? FILESYSTEM_LEFT_WIDTH.max
            : FILESYSTEM_RIGHT_WIDTH.max
        }

        const hardMax =
          side === 'left'
            ? FILESYSTEM_LEFT_WIDTH.max
            : FILESYSTEM_RIGHT_WIDTH.max
        const availableMax =
          layoutRect.width - currentOppositeWidth - FILESYSTEM_MAIN_MIN_WIDTH
        return Math.max(
          side === 'left'
            ? FILESYSTEM_LEFT_WIDTH.min
            : FILESYSTEM_RIGHT_WIDTH.min,
          Math.min(hardMax, availableMax),
        )
      }

      const onMove = (moveEvent: PointerEvent) => {
        const deltaX = moveEvent.clientX - startX
        if (pane === 'context') {
          const nextWidth = clampNumber(
            startLeftWidth + deltaX,
            FILESYSTEM_LEFT_WIDTH.min,
            getMaxWidth('left', rightWidthRef.current),
          )
          setLeftWidth(nextWidth)
          window.localStorage.setItem(
            FILESYSTEM_LEFT_WIDTH_STORAGE_KEY,
            String(nextWidth),
          )
          return
        }

        const nextWidth = clampNumber(
          startRightWidth - deltaX,
          FILESYSTEM_RIGHT_WIDTH.min,
          getMaxWidth('right', leftWidthRef.current),
        )
        setRightWidth(nextWidth)
        window.localStorage.setItem(
          FILESYSTEM_RIGHT_WIDTH_STORAGE_KEY,
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
        <aside className="flex min-h-[180px] min-w-0 shrink-0 basis-[36%] flex-col border-b bg-muted/20 lg:min-h-0 lg:w-[var(--filesystem-left-width)] lg:min-w-[var(--filesystem-left-width)] lg:basis-auto lg:border-b-0">
          <ContextExplorerHeader
            activeTaskCount={activeTaskCount}
            hasActiveTasks={hasActiveTasks}
            hasTasks={canUseLocalTools && tasks.length > 0}
            isRefreshing={listQuery.isFetching}
            isRefreshingTasks={isRefreshingTasks}
            onAddResource={
              canUseLocalTools ? () => setUploadDialogOpen(true) : undefined
            }
            onOpenProcessingTasks={handleOpenProcessingTasks}
            onOpenSearch={handleOpenSearch}
            onRefresh={() => void handleRefreshContextTree()}
          />
          <div className="min-h-0 flex-1">
            <ContextTree
              currentUri={currentUri}
              selectedFileUri={
                selectedFile && !selectedFile.isDir ? selectedFile.uri : null
              }
              expandedKeys={expandedKeys}
              onExpandedKeysChange={handleExpandedKeysChange}
              onSelectDirectory={handleSelectDirectory}
              onSelectFile={handleSelectFile}
            />
          </div>
        </aside>
        <FilesystemResizeHandle
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
            {canUseLocalTools &&
              selectedIsDirectory &&
              /^viking:\/\/(?:resources(?:\/|$)|user\/[^/]+\/resources(?:\/|$))/.test(
                selectedUri,
              ) && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  aria-label={t('compile:compileThisFolder')}
                  onClick={() =>
                    void navigate({
                      to: '/compile/new',
                      search: { from: selectedUri },
                    })
                  }
                >
                  <WandSparkles className="size-4" />
                  <span className="hidden sm:inline">
                    {t('compile:compileThisFolder')}
                  </span>
                </Button>
              )}
            {canManageSelectedUri ? (
              <Button
                type="button"
                size="icon-sm"
                variant="ghost"
                title={t('dataManagement:historyTitle')}
                aria-label={t('dataManagement:historyTitle')}
                onClick={() => setDataManagementMode('history')}
              >
                <HistoryIcon className="size-4" />
              </Button>
            ) : null}
            {canTransferSelectedUri ? (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                title={t('dataManagement:transferTitle')}
                aria-label={t('dataManagement:transferTitle')}
                onClick={() => setDataManagementMode('transfer')}
              >
                <PackageIcon className="size-4" />
                <span className="hidden sm:inline">
                  {t('dataManagement:transferShortLabel')}
                </span>
              </Button>
            ) : null}
            {canUseCliPanel ? (
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
                {canUseLocalTools ? (
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
                ) : null}
              </div>
            ) : null}
            {canUseCliPanel && rightCollapsed ? (
              <Button
                type="button"
                size="icon-sm"
                variant="ghost"
                className="hidden shrink-0 lg:inline-flex"
                title={t('actionPanel.expand')}
                aria-label={t('actionPanel.expand')}
                onClick={toggleRightCollapsed}
              >
                <PanelRightOpenIcon className="size-4" />
              </Button>
            ) : null}
          </div>
          <div className="min-h-0 flex-1">
            <LazyFilePreview
              defaultReadLimit={canUseLocalTools ? -1 : 10000}
              file={selectedFile}
              hideDirectoryHeader
              onClose={() => setSelectedFile(null)}
              onNavigate={(uri) => void revealResource(uri)}
              showCloseButton={false}
            />
          </div>
        </main>
        {canUseCliPanel && !rightCollapsed ? (
          <FilesystemResizeHandle
            active={resizingPane === 'action'}
            label={t('resizeAction')}
            onPointerDown={(event) => handleResizeStart('action', event)}
          />
        ) : null}

        {!isCompactLayout && canUseCliPanel && !rightCollapsed ? (
          <aside className="hidden min-h-0 min-w-0 flex-col bg-muted/15 lg:flex lg:w-[var(--filesystem-right-width)] lg:min-w-[var(--filesystem-right-width)]">
            <FilesystemActionPanel
              activePanel={activePanel}
              currentUri={currentUri}
              entries={entries}
              onCollapse={toggleRightCollapsed}
              onOpenResource={revealResource}
              onPanelChange={handlePanelChange}
              onSessionChange={(sessionId) =>
                syncSearch({ session: sessionId })
              }
              openingUri={openingUri}
              sessionId={search.session}
              showAgentTab={canUseLocalTools}
            />
          </aside>
        ) : null}
      </div>

      {canUseCliPanel && isCompactLayout ? (
        <FilesystemMobileActionScreen
          activePanel={activePanel}
          currentUri={currentUri}
          entries={entries}
          onClose={() => setActionPanelOpen(false)}
          onOpenResource={revealResource}
          onPanelChange={handlePanelChange}
          onSessionChange={(sessionId) => syncSearch({ session: sessionId })}
          open={actionPanelOpen}
          openingUri={openingUri}
          sessionId={search.session}
          showAgentTab={canUseLocalTools}
        />
      ) : null}

      {canUseLocalTools ? (
        <>
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
        </>
      ) : null}
      {dataManagementMode === 'compare' ? (
        <SnapshotComparisonDialog
          key={`${identityScopeKey}:${selectedUri}:compare`}
          onClose={() => setDataManagementMode(null)}
          uri={selectedUri}
        />
      ) : dataManagementMode ? (
        <DataManagementDialog
          key={`${identityScopeKey}:${dataManagementMode === 'transfer' ? transferUri : selectedUri}:${dataManagementMode}`}
          mode={dataManagementMode}
          onClose={() => setDataManagementMode(null)}
          onCompare={() => setDataManagementMode('compare')}
          onChanged={() => void invalidateAll()}
          uri={dataManagementMode === 'transfer' ? transferUri : selectedUri}
          isDirectory={dataManagementMode === 'transfer' || selectedIsDirectory}
        />
      ) : null}
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

function useIsCompactFilesystemLayout() {
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

function FilesystemActionPanel({
  activePanel,
  currentUri,
  entries,
  onCollapse,
  onOpenResource,
  onPanelChange,
  onSessionChange,
  openingUri,
  sessionId,
  showAgentTab,
}: {
  activePanel: FilesystemPanel
  currentUri: string
  entries: VikingFsEntry[]
  onCollapse: () => void
  onOpenResource: ResourceOpenHandler
  onPanelChange: (panel: FilesystemPanel) => void
  onSessionChange: (sessionId: string) => void
  openingUri: string | null
  sessionId?: string
  showAgentTab: boolean
}) {
  const { t } = useTranslation('filesystem')
  const [toolbarContainer, setToolbarContainer] =
    useState<HTMLDivElement | null>(null)

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex h-14 shrink-0 items-center gap-2 border-b px-3">
        <FilesystemActionTabs
          activePanel={activePanel}
          onPanelChange={onPanelChange}
          showAgentTab={showAgentTab}
        />
        <div
          ref={setToolbarContainer}
          className="ml-auto flex min-w-0 items-center gap-1"
        />
        <Button
          type="button"
          size="icon-sm"
          variant="ghost"
          className="shrink-0"
          title={t('actionPanel.collapse')}
          aria-label={t('actionPanel.collapse')}
          onClick={onCollapse}
        >
          <PanelRightCloseIcon className="size-4" />
        </Button>
      </div>

      <FilesystemActionContent
        activePanel={activePanel}
        currentUri={currentUri}
        entries={entries}
        onOpenResource={onOpenResource}
        onSessionChange={onSessionChange}
        openingUri={openingUri}
        sessionId={sessionId}
        toolbarContainer={toolbarContainer}
        showAgentTab={showAgentTab}
      />
    </div>
  )
}

function FilesystemMobileActionScreen({
  activePanel,
  currentUri,
  entries,
  onClose,
  onOpenResource,
  onPanelChange,
  onSessionChange,
  open,
  openingUri,
  sessionId,
  showAgentTab,
}: {
  activePanel: FilesystemPanel
  currentUri: string
  entries: VikingFsEntry[]
  onClose: () => void
  onOpenResource: ResourceOpenHandler
  onPanelChange: (panel: FilesystemPanel) => void
  onSessionChange: (sessionId: string) => void
  open: boolean
  openingUri: string | null
  sessionId?: string
  showAgentTab: boolean
}) {
  const { t } = useTranslation(['filesystem', 'resources'])
  const [toolbarContainer, setToolbarContainer] =
    useState<HTMLDivElement | null>(null)

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
        <FilesystemActionTabs
          activePanel={activePanel}
          onPanelChange={onPanelChange}
          showAgentTab={showAgentTab}
        />
        <div
          ref={setToolbarContainer}
          className="ml-auto flex min-w-0 items-center gap-1"
        />
      </div>
      <div className="min-h-0 flex-1">
        <FilesystemActionContent
          activePanel={activePanel}
          currentUri={currentUri}
          entries={entries}
          onOpenResource={onOpenResource}
          onSessionChange={onSessionChange}
          openingUri={openingUri}
          sessionId={sessionId}
          toolbarContainer={toolbarContainer}
          showAgentTab={showAgentTab}
        />
      </div>
    </div>
  )
}

function FilesystemActionTabs({
  activePanel,
  onPanelChange,
  showAgentTab,
}: {
  activePanel: FilesystemPanel
  onPanelChange: (panel: FilesystemPanel) => void
  showAgentTab: boolean
}) {
  const { t } = useTranslation('filesystem')

  return (
    <div className="inline-flex rounded-lg border bg-background p-1">
      <PanelTab
        active={activePanel === 'terminal'}
        icon={TerminalIcon}
        label={t('tabs.terminal')}
        onClick={() => onPanelChange('terminal')}
      />
      {showAgentTab ? (
        <PanelTab
          active={activePanel === 'agent'}
          icon={BotIcon}
          label={t('tabs.agent')}
          onClick={() => onPanelChange('agent')}
        />
      ) : null}
    </div>
  )
}

function FilesystemActionContent({
  activePanel,
  currentUri,
  entries,
  onOpenResource,
  onSessionChange,
  openingUri,
  sessionId,
  toolbarContainer,
  showAgentTab,
}: {
  activePanel: FilesystemPanel
  currentUri: string
  entries: VikingFsEntry[]
  onOpenResource: ResourceOpenHandler
  onSessionChange: (sessionId: string) => void
  openingUri: string | null
  sessionId?: string
  toolbarContainer: HTMLDivElement | null
  showAgentTab: boolean
}) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      {activePanel === 'terminal' || !showAgentTab ? (
        <TerminalPanel
          currentUri={currentUri}
          entries={entries}
          onOpenResource={onOpenResource}
          onSessionChange={onSessionChange}
          openingUri={openingUri}
          sessionId={sessionId}
          toolbarContainer={toolbarContainer}
        />
      ) : (
        <AgentPanel
          initialSessionId={sessionId}
          onOpenResource={onOpenResource}
          onSessionChange={onSessionChange}
          toolbarContainer={toolbarContainer}
        />
      )}
    </div>
  )
}
