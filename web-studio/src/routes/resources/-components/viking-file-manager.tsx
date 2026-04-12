import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowUp, ChevronRight, Loader2, RefreshCcw, Search, X } from 'lucide-react'

import { Button } from '#/components/ui/button'

import { normalizeDirUri, normalizeFileUri, parentUri } from '../-lib/normalize'
import { useInvalidateVikingFs, useVikingFind, useVikingFsList } from '../-hooks/viking-fm'
import type { VikingFsEntry } from '../-types/viking-fm'
import { FileList } from './file-list'
import { FilePreview } from './file-preview'
import { FileTree } from './file-tree'
import { FindResults } from './find-results'

interface VikingFileManagerProps {
  initialUri?: string
  initialQuery?: string
  initialFile?: string
  onUriChange?: (uri: string) => void
  onQueryChange?: (query: string) => void
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
  initialQuery,
  initialFile,
  onUriChange,
  onQueryChange,
}: VikingFileManagerProps) {
  const [currentUri, setCurrentUri] = useState(
    normalizeDirUri(initialUri || 'viking://'),
  )
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(
    new Set(['viking://']),
  )
  const [selectedFile, setSelectedFile] = useState<VikingFsEntry | null>(null)

  // Search state
  const [searchQuery, setSearchQuery] = useState(initialQuery || '')
  const [activeQuery, setActiveQuery] = useState(initialQuery || '')
  const searchInputRef = useRef<HTMLInputElement>(null)

  const findQuery = useVikingFind(activeQuery, currentUri !== 'viking://' ? currentUri : undefined)
  const isSearchMode = activeQuery.trim().length > 0

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

  // Auto-select file from initialFile prop
  useEffect(() => {
    if (!initialFile) return
    const fileUri = normalizeFileUri(initialFile)
    // Create a minimal VikingFsEntry for the file
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

  const updateUri = (uri: string) => {
    const normalized = normalizeDirUri(uri)
    setCurrentUri(normalized)
    setSelectedFile(null)
  }

  const exitSearchMode = () => {
    setSearchQuery('')
    setActiveQuery('')
    onQueryChange?.('')
  }

  const handleSearchSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const trimmed = searchQuery.trim()
    setActiveQuery(trimmed)
    onQueryChange?.(trimmed)
  }

  const handleNavigateToResource = (uri: string) => {
    exitSearchMode()
    if (isDirectoryUri(uri)) {
      updateUri(uri)
    } else {
      // File URI: navigate to parent dir and select the file
      const dirUri = parentUri(uri)
      updateUri(dirUri)
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
  }

  const listQuery = useVikingFsList(currentUri, {
    output: 'agent',
    showAllHidden: true,
    nodeLimit: 500,
  })
  const { invalidateList } = useInvalidateVikingFs()

  const entries = useMemo(
    () => listQuery.data?.entries || [],
    [listQuery.data?.entries],
  )

  const handleGoParent = () => {
    updateUri(parentUri(currentUri))
  }

  const handleRefresh = async () => {
    await invalidateList()
    await listQuery.refetch()
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
  const showPreview = selectedFile !== null && !isSearchMode

  const [treeWidth, setTreeWidth] = useState(280)
  const dragging = useRef(false)

  const handleResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    dragging.current = true
    const startX = e.clientX
    const startWidth = treeWidth

    const onMove = (ev: MouseEvent) => {
      const newWidth = Math.min(Math.max(startWidth + ev.clientX - startX, 160), 600)
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
  }, [treeWidth])

  // --- Search bar ---
  const searchBar = (
    <form onSubmit={handleSearchSubmit} className="flex items-center gap-1.5">
      <div className="relative flex items-center">
        <Search className="pointer-events-none absolute left-2 size-3.5 text-muted-foreground" />
        <input
          ref={searchInputRef}
          type="text"
          placeholder="语义搜索..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="h-7 w-48 rounded-md border bg-background pl-7 pr-7 text-sm outline-none transition-colors placeholder:text-muted-foreground focus:border-primary focus:ring-1 focus:ring-primary/30"
        />
        {searchQuery && (
          <button
            type="button"
            className="absolute right-1.5 rounded-sm p-0.5 text-muted-foreground hover:text-foreground"
            onClick={exitSearchMode}
          >
            <X className="size-3" />
          </button>
        )}
      </div>
    </form>
  )

  // --- Main content (right side) ---
  const mainContent = () => {
    if (showPreview) {
      return (
        <section className="flex min-h-0 min-w-0 flex-1 flex-col">
          <div className="mx-auto min-h-0 w-full max-w-5xl flex-1">
            <FilePreview
              file={selectedFile}
              onClose={() => setSelectedFile(null)}
              showCloseButton={false}
            />
          </div>
        </section>
      )
    }

    return (
      <section className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="flex h-10 items-center gap-1 border-b px-3">
          {!showTree && (
            <>
              <Button variant="ghost" size="icon" className="size-7" title="返回父级" onClick={handleGoParent}>
                <ArrowUp className="size-4" />
              </Button>
              <Button variant="ghost" size="icon" className="size-7" title="刷新目录" onClick={() => void handleRefresh()}>
                <RefreshCcw className="size-4" />
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
                  onClick={() => {
                    if (isSearchMode) exitSearchMode()
                    updateUri(crumb.uri)
                  }}
                >
                  {crumb.label}
                </button>
              </span>
            ))}
          </nav>
          <div className="ml-auto">{searchBar}</div>
        </div>

        <div className="min-h-0 flex-1 overflow-auto">
          {isSearchMode ? (
            findQuery.isLoading ? (
              <div className="flex items-center justify-center gap-2 py-8 text-sm text-muted-foreground">
                <Loader2 className="size-4 animate-spin" />
                <span>搜索中...</span>
              </div>
            ) : findQuery.error ? (
              <div className="px-4 py-8 text-center text-sm text-destructive">
                搜索出错: {String(findQuery.error)}
              </div>
            ) : findQuery.data ? (
              <FindResults
                data={findQuery.data}
                onNavigate={handleNavigateToResource}
              />
            ) : null
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
    )
  }

  return (
    <div className="-mx-4 -mt-6 -mb-4 md:-mx-6 flex h-[calc(100vh-3.5rem)] flex-col">
      <div className="flex min-h-0 flex-1">
        {showTree && (
          <>
            <section className="flex min-h-0 flex-col bg-muted/30" style={{ width: treeWidth, minWidth: treeWidth }}>
              <div className="flex h-10 items-center gap-1 border-b px-2">
                <Button variant="ghost" size="icon" className="size-7" title="返回父级" onClick={handleGoParent}>
                  <ArrowUp className="size-4" />
                </Button>
                <Button variant="ghost" size="icon" className="size-7" title="刷新目录" onClick={() => void handleRefresh()}>
                  <RefreshCcw className="size-4" />
                </Button>
              </div>
              <div className="min-h-0 flex-1">
                <FileTree
                  currentUri={currentUri}
                  expandedKeys={expandedKeys}
                  onExpandedKeysChange={setExpandedKeys}
                  onSelectDirectory={updateUri}
                />
              </div>
            </section>
            <div
              className="w-1 shrink-0 cursor-col-resize bg-transparent transition-colors hover:bg-primary/20 active:bg-primary/30"
              onMouseDown={handleResizeStart}
            />
          </>
        )}

        {mainContent()}
      </div>
    </div>
  )
}
