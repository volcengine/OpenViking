import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowUp, ChevronRight, RefreshCcw } from 'lucide-react'

import { Button } from '#/components/ui/button'

import { normalizeDirUri, parentUri } from '../-lib/normalize'
import { useInvalidateVikingFs, useVikingFsList } from '../-hooks/viking-fm'
import type { VikingFsEntry } from '../-types/viking-fm'
import { FileList } from './file-list'
import { FilePreview } from './file-preview'
import { FileTree } from './file-tree'

interface VikingFileManagerProps {
  initialUri?: string
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

export function VikingFileManager({
  initialUri,
  onUriChange,
}: VikingFileManagerProps) {
  const [currentUri, setCurrentUri] = useState(
    normalizeDirUri(initialUri || 'viking://'),
  )
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(
    new Set(['viking://']),
  )
  const [selectedFile, setSelectedFile] = useState<VikingFsEntry | null>(null)

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

  const updateUri = (uri: string) => {
    const normalized = normalizeDirUri(uri)
    setCurrentUri(normalized)
    setSelectedFile(null)
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
  const showPreview = selectedFile !== null

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

        {showPreview ? (
          <section className="flex min-h-0 min-w-0 flex-1 flex-col">
            <div className="mx-auto min-h-0 w-full max-w-5xl flex-1">
              <FilePreview
                file={selectedFile}
                onClose={() => setSelectedFile(null)}
                showCloseButton={false}
              />
            </div>
          </section>
        ) : (
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
                      onClick={() => updateUri(crumb.uri)}
                    >
                      {crumb.label}
                    </button>
                  </span>
                ))}
              </nav>
            </div>
            <div className="min-h-0 flex-1 overflow-auto">
              <FileList
                entries={entries}
                selectedFileUri={null}
                onOpenDirectory={updateUri}
                onOpenFile={(file) => setSelectedFile(file)}
              />
            </div>
          </section>
        )}
      </div>
    </div>
  )
}
