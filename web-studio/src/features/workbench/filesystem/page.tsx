import type { FilePreviewAction } from '@openviking/react/components/file-preview'
import { StudioHeaderActions } from '#/app/layouts/studio-header-actions'
import { useQueryClient } from '@tanstack/react-query'
import styles from './workbench.module.css'
import { WorkbenchDirectory } from './components/workbench-directory'
import { ExperienceDetail } from '#/features/workbench/agent-experience/$experienceUri'
import {
  needsExperienceDetailTab,
  isSystemUri,
  usesWorkbenchDirectoryView,
} from './lib/document-kind'
import { useDocumentGuard } from './lib/use-document-guard'
import { showErrorToast } from '#/shared/lib/error-toast'
import { getRouteApi, useNavigate, useBlocker } from '@tanstack/react-router'
import {
  GalleryVerticalEndIcon,
  PencilIcon,
  ArrowLeftIcon,
  HistoryIcon,
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
import { WorkbenchFileBrowser } from './components/workbench-file-browser'
import { SharedFilePreview } from './components/shared-file-preview'
import {
  ResourceUploadProvider,
  useResourceUpload,
} from '#/features/workbench/resources/hooks/use-resource-upload'
import { useInvalidateVikingFs } from '#/features/workbench/resources/hooks/viking-fm'
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
import { FilesystemResizeHandle } from '#/features/workbench/filesystem/components/filesystem-resize-handle'
import { DeleteDocumentDialog } from './components/delete-document-dialog'
import { DataManagementDialog } from '#/features/workbench/filesystem/components/data-management-dialog'
import { SnapshotComparisonDialog } from '#/features/workbench/filesystem/components/snapshot-comparison-dialog'
import { WorkbenchConsole } from '#/features/workbench/filesystem/components/workbench-console'
import {
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
  isDirectoryLevelFile,
  normalizeFilesystemResourceUri,
  readStoredNumber,
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
  const [resourceDialogOpen, setResourceDialogOpen] = useState(false)
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
  const [rightWidth, setRightWidth] = useState(() =>
    readStoredNumber(
      FILESYSTEM_RIGHT_WIDTH_STORAGE_KEY,
      FILESYSTEM_RIGHT_WIDTH.default,
      FILESYSTEM_RIGHT_WIDTH.min,
      FILESYSTEM_RIGHT_WIDTH.max,
    ),
  )
  const [resizing, setResizing] = useState(false)
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
  const activeResizeTeardownRef = useRef<(() => void) | null>(null)
  const rightWidthRef = useRef(rightWidth)
  rightWidthRef.current = rightWidth

  const [refreshKey, setRefreshKey] = useState(0)
  const refreshBrowser = useCallback(() => setRefreshKey((key) => key + 1), [])
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
      refreshBrowser()
      void invalidateAll()
    },
    [identityScopeKey, queryClient, invalidateAll, refreshBrowser],
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

  const navigationVersion = useRef(0)
  useEffect(() => {
    return () => {
      navigationVersion.current += 1
    }
  }, [identityScopeKey, search.file, search.uri])

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
  }, [search.file, search.uri])

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
    async (rawUri: string, expandPath = false) => {
      const cleaned = cleanVikingUri(rawUri)
      if (!cleaned) return

      const targetUri = normalizeFilesystemResourceUri(cleaned)
      const version = ++navigationVersion.current
      try {
        const stat = await fetchFsStat(targetUri, { throwOnError: true })
        if (version !== navigationVersion.current) return
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
        syncSearch({
          file: isDir ? undefined : normalized,
          uri: nextCurrentUri,
        })
        if (expandPath) refreshBrowser()
      } catch (error) {
        if (version === navigationVersion.current)
          showErrorToast(error, t('readFailed', { uri: cleaned }))
      }
    },
    [syncSearch, t, confirmDocumentNavigation, refreshBrowser],
  )

  const openExternalResource = useCallback(
    (uri: string) => revealResource(uri, true),
    [revealResource],
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

  const handleNavigateDirectory = useCallback(
    (rawUri: string) => {
      navigationVersion.current += 1
      const normalized = normalizeDirUri(rawUri)
      if (!confirmDocumentNavigation(normalized)) return
      setCurrentUri(normalized)
      setSelectedFile(createEntryFromUri(normalized, true))
      syncSearch({ file: undefined, uri: normalized })
      refreshBrowser()
    },
    [syncSearch, confirmDocumentNavigation, refreshBrowser],
  )

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      // FileBrowser owns Cmd/Ctrl+K. Keep Cmd/Ctrl+F scoped to the current directory.
      if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== 'f')
        return
      if (event.target instanceof Element && event.target.closest('.cm-editor'))
        return
      event.preventDefault()
      setFindPaletteOpen(true)
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
    setResourceDialogOpen(false)
  }, [selectedUri])
  const canTransferSelectedUri = transferUri.startsWith('viking://resources/')
  const specialDirectory =
    selectedIsDirectory && usesWorkbenchDirectoryView(selectedUri)
  const specialExperience = needsExperienceDetailTab(selectedUri)
  const structuredFile = !selectedIsDirectory && /\.jsonl$/i.test(selectedUri)
  const canEditFile =
    !selectedIsDirectory &&
    !isSystemUri(selectedUri) &&
    !/\/experiences(?:\/|$)/i.test(selectedUri) &&
    /\.(?:md|json|jsonl|txt|yaml|yml)$/i.test(selectedUri)
  const resourceDialogTitle = specialDirectory
    ? t('ide.directoryContents')
    : specialExperience
      ? t('ide.experienceDetails')
      : structuredFile
        ? t('ide.structuredPreview')
        : t('resources:filePreview.edit')
  const layoutStyle = useMemo(
    () =>
      ({
        '--filesystem-right-width': `${rightWidth}px`,
      }) as CSSProperties,
    [rightWidth],
  )

  const handleResizeStart = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      event.preventDefault()
      event.currentTarget.setPointerCapture(event.pointerId)
      setResizing(true)

      const startX = event.clientX
      const startRightWidth = rightWidthRef.current
      const layoutRect = layoutRef.current?.getBoundingClientRect()

      const maxWidth = Math.max(
        FILESYSTEM_RIGHT_WIDTH.min,
        Math.min(
          FILESYSTEM_RIGHT_WIDTH.max,
          (layoutRect?.width ?? 0) - FILESYSTEM_MAIN_MIN_WIDTH,
        ),
      )

      const onMove = (moveEvent: PointerEvent) => {
        const nextWidth = clampNumber(
          startRightWidth - (moveEvent.clientX - startX),
          FILESYSTEM_RIGHT_WIDTH.min,
          maxWidth,
        )
        setRightWidth(nextWidth)
        window.localStorage.setItem(
          FILESYSTEM_RIGHT_WIDTH_STORAGE_KEY,
          String(nextWidth),
        )
      }

      const onUp = () => {
        activeResizeTeardownRef.current = null
        setResizing(false)
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

  const resourceActions: FilePreviewAction[] = []
  if (specialDirectory || specialExperience || structuredFile || canEditFile) {
    resourceActions.push({
      key: 'studio-resource-detail',
      icon: <PencilIcon size={16} />,
      label: resourceDialogTitle,
      onClick: () => setResourceDialogOpen(true),
    })
  }
  resourceActions.push({
    key: 'studio-history',
    icon: <HistoryIcon size={16} />,
    label: t('dataManagement:historyMenu'),
    onClick: () => setDataManagementMode('history'),
  })

  const transferActions: FilePreviewAction[] = canTransferSelectedUri
    ? [
        {
          key: 'studio-import-pack',
          icon: <DownloadIcon size={16} />,
          label: t('dataManagement:importMenu'),
          onClick: () => {
            setTransferTab('import')
            setDataManagementMode('transfer')
          },
        },
        {
          key: 'studio-export-pack',
          icon: <UploadIcon size={16} />,
          label: t('dataManagement:exportMenu'),
          onClick: () => {
            setTransferTab('export')
            setDataManagementMode('transfer')
          },
        },
      ]
    : []
  if (canUseLocalTools && selectedIsDirectory) {
    transferActions.push({
      key: 'studio-add-resource',
      icon: <UploadIcon size={16} />,
      label: t('explorer.addResource'),
      onClick: () => setUploadDialogOpen(true),
    })
    if (
      /^viking:\/\/(?:resources(?:\/|$)|user\/[^/]+\/resources(?:\/|$))/.test(
        selectedUri,
      )
    ) {
      transferActions.push({
        key: 'studio-compile-folder',
        icon: <WandSparkles size={16} />,
        label: t('compile:compileThisFolder'),
        onClick: () =>
          navigate({ to: '/compile/new', search: { from: selectedUri } }),
      })
    }
  }

  const deleteActions: FilePreviewAction[] =
    selectedFile && !selectedIsDirectory && !isSystemUri(selectedUri)
      ? [
          {
            key: 'studio-delete',
            icon: <Trash2Icon size={16} />,
            label: t('dataManagement:deleteDocument'),
            className: styles.deleteAction,
            onClick: () =>
              setDeleteTarget({
                uri: selectedFile.uri,
                name: selectedFile.name,
                dirty: dirtyRef.current,
              }),
          },
        ]
      : []

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
        <WorkbenchFileBrowser
          key={`${identityScopeKey}:${refreshKey}`}
          readOnly
          previewActions={{
            history: false,
            importPack: false,
            exportPack: false,
          }}
          previewSlots={{
            actionItems: ({ defaultActions }) => [
              ...defaultActions,
              ...(selectedFile ? [resourceActions, transferActions] : []),
              deleteActions,
            ],
          }}
          uri={normalizeFileUri(selectedUri)}
          defaultExpandedUri={normalizeFileUri(currentUri)}
          onUriChange={(uri) => {
            if (uri && uri !== normalizeFileUri(selectedUri))
              void revealResource(uri)
          }}
          onImportResources={
            canUseLocalTools ? () => setUploadDialogOpen(true) : undefined
          }
        />
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
        {canUseCliPanel && !rightCollapsed ? (
          <FilesystemResizeHandle
            active={resizing}
            label={t('resizeAction')}
            onPointerDown={handleResizeStart}
          />
        ) : null}

        {!isCompactLayout && canUseCliPanel && !rightCollapsed ? (
          <aside className={styles.assistant}>
            <FilesystemActionPanel
              activePanel={activePanel}
              onCollapse={toggleRightCollapsed}
              onOpenResource={openExternalResource}
              onFilesystemWrite={refreshBrowser}
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
          onOpenResource={openExternalResource}
          onFilesystemWrite={refreshBrowser}
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
                    refreshBrowser()
                    void invalidateList()
                    toast.success(t('addResource.submitted'))
                  }}
                />
              </div>
            </DialogContent>
          </Dialog>
        </>
      ) : null}
      {selectedFile && resourceDialogOpen ? (
        <Dialog
          open
          onOpenChange={(open) => {
            if (!open && confirmDocumentNavigation())
              setResourceDialogOpen(false)
          }}
        >
          <DialogContent className="flex h-[min(80svh,760px)] max-w-[min(90vw,64rem)] flex-col overflow-hidden">
            <DialogHeader>
              <DialogTitle>{resourceDialogTitle}</DialogTitle>
              <DialogDescription className="truncate">
                {selectedUri}
              </DialogDescription>
            </DialogHeader>
            <div className="flex min-h-0 flex-1 flex-col overflow-auto">
              {specialDirectory ? (
                <WorkbenchDirectory
                  uri={selectedUri}
                  onNavigate={openExternalResource}
                />
              ) : specialExperience ? (
                <ExperienceDetail experienceUri={selectedUri} embedded />
              ) : (
                <SharedFilePreview
                  file={selectedFile}
                  readOnly={!canEditFile}
                  onNavigate={openExternalResource}
                  onDirtyChange={handleDirtyChange}
                  onSaved={handleDocumentSaved}
                />
              )}
            </div>
          </DialogContent>
        </Dialog>
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
            refreshBrowser()
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
          onChanged={() => {
            refreshBrowser()
            void invalidateAll()
          }}
          uri={dataManagementMode === 'transfer' ? transferUri : selectedUri}
          isDirectory={dataManagementMode === 'transfer' || selectedIsDirectory}
        />
      ) : null}
      <FindPalette
        ide
        open={findPaletteOpen}
        onClose={() => setFindPaletteOpen(false)}
        onNavigate={openExternalResource}
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
  onFilesystemWrite,
  onPanelChange,
  onSessionChange,
  sessionId,
  showAgentTab,
}: {
  activePanel: FilesystemPanel
  onCollapse: () => void
  onOpenResource: ResourceOpenHandler
  onFilesystemWrite: () => void
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
      onFilesystemWrite={onFilesystemWrite}
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
  onFilesystemWrite,
  onPanelChange,
  onSessionChange,
  open,
  sessionId,
  showAgentTab,
}: {
  activePanel: FilesystemPanel
  onClose: () => void
  onOpenResource: ResourceOpenHandler
  onFilesystemWrite: () => void
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
        onFilesystemWrite={onFilesystemWrite}
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
  onFilesystemWrite,
  onPanelChange,
  onSessionChange,
  sessionId,
  showAgentTab,
  leadingAction,
  trailingAction,
}: {
  activePanel: FilesystemPanel
  onOpenResource: ResourceOpenHandler
  onFilesystemWrite: () => void
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
        onFilesystemWrite={onFilesystemWrite}
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
          onFilesystemWrite={onFilesystemWrite}
          toolbar={toolbar}
        />
      )}
    </AgentPanel>
  )
}
