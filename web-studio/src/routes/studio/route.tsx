import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties, PointerEvent as ReactPointerEvent } from 'react'
import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { BotIcon, ClipboardIcon, TerminalIcon } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '#/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '#/components/ui/dialog'
import { cn } from '#/lib/utils'
import { FilePreview } from '#/routes/resources/-components/file-preview'
import { AddResourceForm } from '#/routes/resources/-components/add-resource-page'
import { ResourceUploadProvider } from '#/routes/resources/-hooks/use-resource-upload'
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
  StudioResizeHandle,
} from './-components/context-explorer'
import { TerminalPanel } from './-components/terminal-panel'
import {
  ROOT_URI,
  STUDIO_LEFT_WIDTH,
  STUDIO_LEFT_WIDTH_STORAGE_KEY,
  STUDIO_MAIN_MIN_WIDTH,
  STUDIO_RIGHT_WIDTH,
  STUDIO_RIGHT_WIDTH_STORAGE_KEY,
} from './-lib/constants'
import type { StudioPanel, StudioSearch } from './-lib/types'
import {
  buildBreadcrumbs,
  clampNumber,
  cleanVikingUri,
  createEntryFromUri,
  getAncestorUris,
  isDirectoryLevelFile,
  mergeExpanded,
  normalizeStudioResourceUri,
  readStoredNumber,
  visibleContextEntries,
} from './-lib/utils'

export const Route = createFileRoute('/studio')({
  validateSearch: (search: Record<string, unknown>): StudioSearch => ({
    file: typeof search.file === 'string' ? search.file : undefined,
    panel:
      search.panel === 'agent' || search.panel === 'terminal'
        ? search.panel
        : undefined,
    session: typeof search.session === 'string' ? search.session : undefined,
    uri: typeof search.uri === 'string' ? search.uri : undefined,
  }),
  component: StudioRoute,
})

function StudioRoute() {
  return (
    <ResourceUploadProvider>
      <StudioWorkbench />
    </ResourceUploadProvider>
  )
}

function StudioWorkbench() {
  const { t } = useTranslation('studio')
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
  const [activePanel, setActivePanel] = useState<StudioPanel>(
    search.panel ?? 'agent',
  )
  const [uploadDialogOpen, setUploadDialogOpen] = useState(false)
  const [openingUri, setOpeningUri] = useState<string | null>(null)
  const layoutRef = useRef<HTMLDivElement>(null)
  const [leftWidth, setLeftWidth] = useState(() =>
    readStoredNumber(
      STUDIO_LEFT_WIDTH_STORAGE_KEY,
      STUDIO_LEFT_WIDTH.default,
      STUDIO_LEFT_WIDTH.min,
      STUDIO_LEFT_WIDTH.max,
    ),
  )
  const [rightWidth, setRightWidth] = useState(() =>
    readStoredNumber(
      STUDIO_RIGHT_WIDTH_STORAGE_KEY,
      STUDIO_RIGHT_WIDTH.default,
      STUDIO_RIGHT_WIDTH.min,
      STUDIO_RIGHT_WIDTH.max,
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
  const { invalidateList } = useInvalidateVikingFs()

  const syncSearch = useCallback(
    (next: {
      file?: string
      panel?: StudioPanel
      session?: string
      uri?: string
    }) => {
      navigate({
        replace: true,
        search: (prev) => ({
          ...prev,
          ...next,
        }),
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

  const revealResource = useCallback(
    async (rawUri: string) => {
      const cleaned = cleanVikingUri(rawUri)
      if (!cleaned) return

      setOpeningUri(cleaned)
      const targetUri = normalizeStudioResourceUri(cleaned)
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
        setSelectedFile(createEntryFromUri(normalized, fallbackIsDir))
        setExpandedKeys((prev) =>
          mergeExpanded(prev, getAncestorUris(nextCurrentUri)),
        )
        syncSearch({
          file: fallbackIsDir ? undefined : normalized,
          uri: nextCurrentUri,
        })
        toast.error(
          error instanceof Error
            ? error.message
            : t('readFailed', { uri: cleaned }),
        )
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
    (panel: StudioPanel) => {
      setActivePanel(panel)
      syncSearch({ panel })
    },
    [syncSearch],
  )

  const breadcrumbs = useMemo(() => {
    const targetUri = selectedFile?.uri ?? currentUri
    const isFile = selectedFile ? !selectedFile.isDir : false
    return buildBreadcrumbs(targetUri, isFile)
  }, [currentUri, selectedFile])

  const selectedUri = selectedFile?.uri ?? currentUri
  const entries = visibleContextEntries(listQuery.data?.entries ?? [])
  const layoutStyle = useMemo(
    () =>
      ({
        '--studio-left-width': `${leftWidth}px`,
        '--studio-right-width': `${rightWidth}px`,
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
            ? STUDIO_LEFT_WIDTH.max
            : STUDIO_RIGHT_WIDTH.max
        }

        const hardMax =
          side === 'left' ? STUDIO_LEFT_WIDTH.max : STUDIO_RIGHT_WIDTH.max
        const availableMax =
          layoutRect.width - currentOppositeWidth - STUDIO_MAIN_MIN_WIDTH
        return Math.max(
          side === 'left' ? STUDIO_LEFT_WIDTH.min : STUDIO_RIGHT_WIDTH.min,
          Math.min(hardMax, availableMax),
        )
      }

      const onMove = (moveEvent: PointerEvent) => {
        const deltaX = moveEvent.clientX - startX
        if (pane === 'context') {
          const nextWidth = clampNumber(
            startLeftWidth + deltaX,
            STUDIO_LEFT_WIDTH.min,
            getMaxWidth('left', rightWidthRef.current),
          )
          setLeftWidth(nextWidth)
          window.localStorage.setItem(
            STUDIO_LEFT_WIDTH_STORAGE_KEY,
            String(nextWidth),
          )
          return
        }

        const nextWidth = clampNumber(
          startRightWidth - deltaX,
          STUDIO_RIGHT_WIDTH.min,
          getMaxWidth('right', leftWidthRef.current),
        )
        setRightWidth(nextWidth)
        window.localStorage.setItem(
          STUDIO_RIGHT_WIDTH_STORAGE_KEY,
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
        className="flex min-h-0 flex-1 flex-col border-t bg-background lg:flex-row"
        style={layoutStyle}
      >
        <aside className="flex min-h-[260px] min-w-0 flex-col border-b bg-muted/20 lg:min-h-0 lg:w-[var(--studio-left-width)] lg:min-w-[var(--studio-left-width)] lg:border-b-0">
          <ContextExplorerHeader
            isRefreshing={listQuery.isFetching}
            onAddResource={() => setUploadDialogOpen(true)}
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
        <StudioResizeHandle
          active={resizingPane === 'context'}
          label={t('resizeContext')}
          onPointerDown={(event) => handleResizeStart('context', event)}
        />

        <main className="flex min-h-[420px] min-w-0 flex-1 flex-col border-b lg:min-h-0 lg:border-b-0">
          <div className="flex min-h-14 items-center gap-3 border-b px-4">
            <nav className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto whitespace-nowrap text-sm">
              {breadcrumbs.map((crumb, index) => (
                <span
                  key={crumb.uri}
                  className="flex shrink-0 items-center gap-1.5"
                >
                  {index > 0 ? (
                    <span className="font-mono text-xs text-muted-foreground/60">
                      /
                    </span>
                  ) : null}
                  <button
                    type="button"
                    className={cn(
                      'rounded px-1.5 py-1 font-mono text-xs transition-colors hover:bg-muted',
                      index === breadcrumbs.length - 1
                        ? 'font-semibold text-foreground'
                        : 'text-muted-foreground',
                    )}
                    onClick={() => void revealResource(crumb.uri)}
                  >
                    {crumb.label}
                  </button>
                </span>
              ))}
            </nav>
            <Button
              type="button"
              size="icon-sm"
              variant="ghost"
              title={t('copyUri')}
              onClick={() => {
                void navigator.clipboard.writeText(selectedUri)
                toast.success(t('copied'))
              }}
            >
              <ClipboardIcon className="size-4" />
            </Button>
          </div>
          <div className="min-h-0 flex-1">
            <FilePreview
              file={selectedFile}
              onClose={() => setSelectedFile(null)}
              showCloseButton={false}
            />
          </div>
        </main>
        <StudioResizeHandle
          active={resizingPane === 'action'}
          label={t('resizeAction')}
          onPointerDown={(event) => handleResizeStart('action', event)}
        />

        <aside className="flex min-h-[460px] min-w-0 flex-col bg-muted/15 lg:min-h-0 lg:w-[var(--studio-right-width)] lg:min-w-[var(--studio-right-width)]">
          <div className="flex h-14 items-center border-b px-3">
            <div className="inline-flex rounded-lg border bg-background p-1">
              <PanelTab
                active={activePanel === 'terminal'}
                icon={TerminalIcon}
                label={t('tabs.terminal')}
                onClick={() => handlePanelChange('terminal')}
              />
              <PanelTab
                active={activePanel === 'agent'}
                icon={BotIcon}
                label={t('tabs.agent')}
                onClick={() => handlePanelChange('agent')}
              />
            </div>
          </div>

          {activePanel === 'terminal' ? (
            <TerminalPanel
              currentUri={currentUri}
              entries={entries}
              onOpenAddResource={() => setUploadDialogOpen(true)}
              onOpenResource={revealResource}
              openingUri={openingUri}
            />
          ) : (
            <AgentPanel
              initialSessionId={search.session}
              onOpenResource={revealResource}
              onSessionChange={(sessionId) =>
                syncSearch({ session: sessionId })
              }
            />
          )}
        </aside>
      </div>

      <Dialog open={uploadDialogOpen} onOpenChange={setUploadDialogOpen}>
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
                setUploadDialogOpen(false)
                void invalidateList()
                toast.success(t('addResource.submitted'))
              }}
            />
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
