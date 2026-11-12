import { StudioHeaderActions } from '#/app/layouts/studio-header-actions'
import { useQueryClient } from '@tanstack/react-query'
import styles from './workbench.module.css'
import { WorkbenchDirectory } from './components/workbench-directory'
import { ExperienceDetail } from '#/features/workbench/agent-experience/$experienceUri'
import {
  isExperienceFile,
  usesWorkbenchDirectoryView,
} from './lib/document-kind'
import { useDocumentGuard } from './lib/use-document-guard'
import { showErrorToast } from '#/shared/lib/error-toast'
import { getRouteApi, useNavigate, useBlocker } from '@tanstack/react-router'
import {
  GalleryVerticalEndIcon,
  MoreHorizontalIcon,
  ArrowLeftIcon,
  HistoryIcon,
  CopyIcon,
  Trash2Icon,
  DownloadIcon,
  PanelRightCloseIcon,
  PanelRightOpenIcon,
  UploadIcon,
  WandSparkles,
} from 'lucide-react'
import type {
  CSSProperties,
  PointerEvent as ReactPointerEvent,
  ReactNode,
} from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { useStudioService } from '#/features/connection/hooks/use-studio-service'
import { AddResourceForm } from '#/features/workbench/resources/components/add-resource-page'
import { FindPalette } from '#/features/workbench/resources/components/find-palette'
import { SharedFilePreview } from './components/shared-file-preview'
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
} from '#/features/workbench/filesystem/components/context-explorer'
import { DeleteDocumentDialog } from './components/delete-document-dialog'
import { DataManagementDialog } from '#/features/workbench/filesystem/components/data-management-dialog'
import { SnapshotComparisonDialog } from '#/features/workbench/filesystem/components/snapshot-comparison-dialog'
import { WorkbenchConsole } from '#/features/workbench/filesystem/components/workbench-console'
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
  const { t } = useTranslation(['filesystem', 'resources', 'tasksPage'])
  const { provider } = useStudioService()
  const canUseLocalTools = provider === 'opensource'
  const canUseCliPanel = canUseLocalTools || provider === 'volcengine'
  const { identityScopeKey } = useAppConnection()
  const queryClient = useQueryClient()
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
  const {
    dirtyRef,
    onDirtyChange: handleDirtyChange,
    confirmNavigation: confirmDocumentNavigation,
  } = useDocumentGuard(selectedFile?.uri, t('ide.discard'))
  useBlocker({
    shouldBlockFn: ({ current, next }) => {
      const previousDocument = current.search as { file?: string; uri?: string }
      const nextDocument = next.search as { file?: string; uri?: string }
      if (
        current.pathname === next.pathname &&
        previousDocument.file === nextDocument.file &&
        previousDocument.uri === nextDocument.uri
      )
        return false
      return !confirmDocumentNavigation()
    },
    enableBeforeUnload: () => dirtyRef.current,
  })
  const [findPaletteOpen, setFindPaletteOpen] = useState(false)
  const [dataManagementMode, setDataManagementMode] = useState<
    'history' | 'transfer' | 'compare' | null
  >(null)
  const [deleteTarget, setDeleteTarget] = useState<{
    uri: string
    name: string
    dirty: boolean
  } | null>(null)
  const [transferTab, setTransferTab] = useState<'import' | 'export'>('export')
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
  const { activeTaskCount, hasActiveTasks, isRefreshingTasks } =
    useResourceUpload()
  const { invalidateAll, invalidateList } = useInvalidateVikingFs()
  const handleDocumentSaved = useCallback(
    (uri: string) => {
      void queryClient.invalidateQueries({
        queryKey: ['agent-experience-content', identityScopeKey, uri],
      })
      void queryClient.invalidateQueries({
        queryKey: ['agent-experience-list', identityScopeKey],
      })
      void invalidateAll()
    },
    [identityScopeKey, queryClient, invalidateAll],
  )

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

        if (!confirmDocumentNavigation(normalized)) return
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
        showErrorToast(error, t('readFailed', { uri: cleaned }))
      }
    },
    [syncSearch, t, confirmDocumentNavigation],
  )

  const handleSelectDirectory = useCallback(
    (entry: VikingFsEntry) => {
      const normalized = normalizeDirUri(entry.uri)
      if (!confirmDocumentNavigation(normalized)) return
      setCurrentUri(normalized)
      setSelectedFile({ ...entry, isDir: true, uri: normalized })
      setExpandedKeys((prev) =>
        mergeExpanded(prev, getAncestorUris(normalized)),
      )
      syncSearch({ file: undefined, uri: normalized })
    },
    [syncSearch, confirmDocumentNavigation],
  )

  const handleSelectFile = useCallback(
    (entry: VikingFsEntry) => {
      const normalized = normalizeFileUri(entry.uri)
      const targetUri = isDirectoryLevelFile(normalized)
        ? normalizeDirUri(parentUri(normalized))
        : normalized
      if (!confirmDocumentNavigation(targetUri)) return
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
    [syncSearch, confirmDocumentNavigation],
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
    const { file, uri, panel, session } = search
    void navigate({ to: '/tasks', search: { file, uri, panel, session } })
  }, [navigate, search])

  const handleOpenSearch = useCallback(() => {
    setFindPaletteOpen(true)
  }, [])

  const handleNavigateDirectory = useCallback(
    (rawUri: string) => {
      const normalized = normalizeDirUri(rawUri)
      if (!confirmDocumentNavigation(normalized)) return
      setCurrentUri(normalized)
      setSelectedFile(createEntryFromUri(normalized, true))
      setExpandedKeys((prev) =>
        mergeExpanded(prev, getAncestorUris(normalized)),
      )
      syncSearch({ file: undefined, uri: normalized })
    },
    [syncSearch, confirmDocumentNavigation],
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
  const canTransferSelectedUri = transferUri.startsWith('viking://resources/')
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
    <div className={styles.workbench}>
      <StudioHeaderActions>
        <Button
          type="button"
          size="icon-sm"
          variant="ghost"
          className="relative size-8 rounded-full text-foreground/80"
          title={t('title', { ns: 'tasksPage' })}
          aria-label={t('title', { ns: 'tasksPage' })}
          onClick={handleOpenProcessingTasks}
        >
          <GalleryVerticalEndIcon
            className={
              hasActiveTasks || isRefreshingTasks
                ? 'size-[18px] text-primary'
                : 'size-[18px]'
            }
          />
          {activeTaskCount > 0 ? (
            <span className="absolute -top-1 -right-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[10px] leading-none text-primary-foreground">
              {activeTaskCount}
            </span>
          ) : null}
        </Button>
      </StudioHeaderActions>
      <div ref={layoutRef} className={styles.layout} style={layoutStyle}>
        <aside className={styles.explorer}>
          <ContextExplorerHeader
            onAddResource={
              canUseLocalTools ? () => setUploadDialogOpen(true) : undefined
            }
          />
          <div className="min-h-0 flex-1">
            <ContextTree
              isRefreshing={listQuery.isFetching}
              onOpenSearch={handleOpenSearch}
              onRefresh={handleRefreshContextTree}
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

        <main className={styles.document}>
          {selectedFile ? (
            <SharedFilePreview
              key={selectedFile.uri}
              file={selectedFile}
              onDirtyChange={handleDirtyChange}
              onSaved={handleDocumentSaved}
              onNavigate={(uri) => {
                void revealResource(uri)
              }}
              defaultReadLimit={canUseLocalTools ? -1 : 10000}
              extraActions={(previewActions) => (
                <>
                  <details className={styles.moreMenu}>
                    <summary aria-label={t('ide.more')} title={t('ide.more')}>
                      <MoreHorizontalIcon className="size-3.5" />
                    </summary>
                    <div
                      className={styles.moreMenuContent}
                      onClick={(event) => {
                        if (
                          (event.target as HTMLElement).closest(
                            'button:not(:disabled)',
                          )
                        )
                          event.currentTarget
                            .closest('details')
                            ?.removeAttribute('open')
                      }}
                    >
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        disabled={
                          !previewActions?.canCopyContent || previewActions.busy
                        }
                        onClick={async () => {
                          const copied = await previewActions?.copyContent()
                          if (copied)
                            toast.success(t('dataManagement:copySucceeded'))
                          else toast.error(t('dataManagement:copyFailed'))
                        }}
                      >
                        <CopyIcon className="size-4" />
                        <span>{t('dataManagement:copyContent')}</span>
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        onClick={() => setDataManagementMode('history')}
                      >
                        <HistoryIcon className="size-4" />
                        <span>{t('dataManagement:historyMenu')}</span>
                      </Button>
                      {canTransferSelectedUri ? (
                        <>
                          <div
                            role="separator"
                            className={styles.moreMenuSeparator}
                          />
                          <Button
                            type="button"
                            size="sm"
                            variant="ghost"
                            onClick={() => {
                              setTransferTab('import')
                              setDataManagementMode('transfer')
                            }}
                          >
                            <DownloadIcon className="size-4" />
                            <span>{t('dataManagement:importMenu')}</span>
                          </Button>
                          <Button
                            type="button"
                            size="sm"
                            variant="ghost"
                            onClick={() => {
                              setTransferTab('export')
                              setDataManagementMode('transfer')
                            }}
                          >
                            <UploadIcon className="size-4" />
                            <span>{t('dataManagement:exportMenu')}</span>
                          </Button>
                        </>
                      ) : null}
                      {canUseLocalTools && selectedIsDirectory ? (
                        <div
                          role="separator"
                          className={styles.moreMenuSeparator}
                        />
                      ) : null}
                      {canUseLocalTools && selectedIsDirectory ? (
                        <Button
                          type="button"
                          size="sm"
                          aria-label={t('explorer.addResource')}
                          variant="ghost"
                          onClick={() => setUploadDialogOpen(true)}
                        >
                          <UploadIcon className="size-4" />
                          <span>{t('explorer.addResource')}</span>
                        </Button>
                      ) : null}
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
                            <span>{t('compile:compileThisFolder')}</span>
                          </Button>
                        )}
                      {!selectedIsDirectory ? (
                        <>
                          <div
                            role="separator"
                            className={styles.moreMenuSeparator}
                          />
                          <Button
                            type="button"
                            size="sm"
                            variant="ghost"
                            className={styles.moreMenuDanger}
                            onClick={() =>
                              setDeleteTarget({
                                uri: selectedFile.uri,
                                name: selectedFile.name,
                                dirty: dirtyRef.current,
                              })
                            }
                          >
                            <Trash2Icon className="size-4" />
                            <span>{t('dataManagement:deleteDocument')}</span>
                          </Button>
                        </>
                      ) : null}
                    </div>
                  </details>
                  {canUseCliPanel && (isCompactLayout || rightCollapsed) ? (
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      title={t('actionPanel.expand')}
                      onClick={() =>
                        isCompactLayout
                          ? handleOpenActionPanel(activePanel)
                          : toggleRightCollapsed()
                      }
                    >
                      <PanelRightOpenIcon className="size-4" />
                    </Button>
                  ) : null}
                </>
              )}
              content={
                selectedIsDirectory &&
                usesWorkbenchDirectoryView(selectedUri) ? (
                  <WorkbenchDirectory
                    key={selectedUri}
                    uri={selectedUri}
                    onNavigate={(uri) => {
                      void revealResource(uri)
                    }}
                  />
                ) : isExperienceFile(selectedFile.uri) ? (
                  <ExperienceDetail experienceUri={selectedFile.uri} embedded />
                ) : undefined
              }
            />
          ) : canUseCliPanel && (isCompactLayout || rightCollapsed) ? (
            <Button
              variant="ghost"
              size="icon-sm"
              title={t('actionPanel.expand')}
              onClick={() =>
                isCompactLayout
                  ? handleOpenActionPanel(activePanel)
                  : toggleRightCollapsed()
              }
            >
              <PanelRightOpenIcon className="size-4" />
            </Button>
          ) : null}
        </main>
        {canUseCliPanel && !rightCollapsed ? (
          <FilesystemResizeHandle
            active={resizingPane === 'action'}
            label={t('resizeAction')}
            onPointerDown={(event) => handleResizeStart('action', event)}
          />
        ) : null}

        {!isCompactLayout && canUseCliPanel && !rightCollapsed ? (
          <aside className={styles.assistant}>
            <FilesystemActionPanel
              activePanel={activePanel}
              onCollapse={toggleRightCollapsed}
              onOpenResource={revealResource}
              onPanelChange={handlePanelChange}
              onSessionChange={(sessionId) =>
                syncSearch({ session: sessionId })
              }
              sessionId={search.session}
              showAgentTab={canUseLocalTools}
            />
          </aside>
        ) : null}
      </div>

      {canUseCliPanel && isCompactLayout ? (
        <FilesystemMobileActionScreen
          activePanel={activePanel}
          onClose={() => setActionPanelOpen(false)}
          onOpenResource={revealResource}
          onPanelChange={handlePanelChange}
          onSessionChange={(sessionId) => syncSearch({ session: sessionId })}
          open={actionPanelOpen}
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
        </>
      ) : null}
      {deleteTarget ? (
        <DeleteDocumentDialog
          key={`${identityScopeKey}:${deleteTarget.uri}`}
          {...deleteTarget}
          onClose={() => setDeleteTarget(null)}
          onDeleted={(uri) => {
            setDeleteTarget(null)
            if (selectedFileRef.current?.uri === uri) {
              handleDirtyChange(false)
              setSelectedFile(null)
              const directory = normalizeDirUri(parentUri(uri))
              setCurrentUri(directory)
              syncSearch({ file: undefined, uri: directory })
            }
            toast.success(t('dataManagement:documentDeleted'))
            void invalidateAll()
          }}
        />
      ) : null}
      {dataManagementMode === 'compare' ? (
        <SnapshotComparisonDialog
          key={`${identityScopeKey}:${selectedUri}:compare`}
          onClose={() => setDataManagementMode(null)}
          uri={selectedUri}
        />
      ) : dataManagementMode ? (
        <DataManagementDialog
          key={`${identityScopeKey}:${dataManagementMode === 'transfer' ? transferUri : selectedUri}:${dataManagementMode}:${transferTab}`}
          mode={dataManagementMode}
          initialTransferTab={transferTab}
          onClose={() => setDataManagementMode(null)}
          onCompare={() => setDataManagementMode('compare')}
          onChanged={() => void invalidateAll()}
          uri={dataManagementMode === 'transfer' ? transferUri : selectedUri}
          isDirectory={dataManagementMode === 'transfer' || selectedIsDirectory}
        />
      ) : null}
      <FindPalette
        ide
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
      ? window.matchMedia('(max-width: 1279px)').matches
      : false,
  )

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') {
      return
    }
    const mql = window.matchMedia('(max-width: 1279px)')
    const onChange = () => setIsCompact(mql.matches)
    onChange()
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [])

  return isCompact
}

function FilesystemActionPanel({
  activePanel,
  onCollapse,
  onOpenResource,
  onPanelChange,
  onSessionChange,
  sessionId,
  showAgentTab,
}: {
  activePanel: FilesystemPanel
  onCollapse: () => void
  onOpenResource: ResourceOpenHandler
  onPanelChange: (panel: FilesystemPanel) => void
  onSessionChange: (sessionId: string) => void
  sessionId?: string
  showAgentTab: boolean
}) {
  const { t } = useTranslation('filesystem')
  const collapseButton = (
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
  )
  return (
    <FilesystemActionContent
      activePanel={activePanel}
      onOpenResource={onOpenResource}
      onPanelChange={onPanelChange}
      onSessionChange={onSessionChange}
      sessionId={sessionId}
      showAgentTab={showAgentTab}
      trailingAction={collapseButton}
    />
  )
}

function FilesystemMobileActionScreen({
  activePanel,
  onClose,
  onOpenResource,
  onPanelChange,
  onSessionChange,
  open,
  sessionId,
  showAgentTab,
}: {
  activePanel: FilesystemPanel
  onClose: () => void
  onOpenResource: ResourceOpenHandler
  onPanelChange: (panel: FilesystemPanel) => void
  onSessionChange: (sessionId: string) => void
  open: boolean
  sessionId?: string
  showAgentTab: boolean
}) {
  const { t } = useTranslation('resources')
  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex min-h-0 flex-col bg-background xl:hidden">
      <FilesystemActionContent
        activePanel={activePanel}
        onOpenResource={onOpenResource}
        onPanelChange={onPanelChange}
        onSessionChange={onSessionChange}
        sessionId={sessionId}
        showAgentTab={showAgentTab}
        leadingAction={
          <Button
            type="button"
            size="icon-sm"
            variant="ghost"
            title={t('dirBrowser.back')}
            aria-label={t('dirBrowser.back')}
            onClick={onClose}
          >
            <ArrowLeftIcon className="size-4" />
          </Button>
        }
      />
    </div>
  )
}

function FilesystemActionContent({
  activePanel,
  onOpenResource,
  onPanelChange,
  onSessionChange,
  sessionId,
  showAgentTab,
  leadingAction,
  trailingAction,
}: {
  activePanel: FilesystemPanel
  onOpenResource: ResourceOpenHandler
  onPanelChange: (panel: FilesystemPanel) => void
  onSessionChange: (sessionId: string) => void
  sessionId?: string
  showAgentTab: boolean
  leadingAction?: ReactNode
  trailingAction?: ReactNode
}) {
  const toolbar = (
    <div className="flex min-w-0 items-center justify-end gap-2">
      {leadingAction}
      {trailingAction}
    </div>
  )

  if (!showAgentTab) {
    return (
      <WorkbenchConsole
        mode="command"
        onOpenResource={onOpenResource}
        toolbar={toolbar}
      />
    )
  }

  return (
    <AgentPanel initialSessionId={sessionId} onSessionChange={onSessionChange}>
      {(conversation) => (
        <WorkbenchConsole
          conversation={conversation}
          mode={activePanel === 'agent' ? 'conversation' : 'command'}
          onModeChange={(mode) =>
            onPanelChange(mode === 'conversation' ? 'agent' : 'terminal')
          }
          onOpenResource={onOpenResource}
          toolbar={toolbar}
        />
      )}
    </AgentPanel>
  )
}
