import { useEffect, useMemo, useState } from 'react'
import { ArrowUp, RefreshCcw } from 'lucide-react'

import { Button } from '#/components/ui/button'
import { Dialog, DialogContent, DialogTitle } from '#/components/ui/dialog'
import { LegacyPageShell } from '#/components/legacy/shared/page-shell'
import {
  normalizeDirUri,
  parentUri,
  useInvalidateVikingFs,
  useVikingFsList,
} from '#/lib/viking-fm'
import type { VikingFsEntry } from '#/lib/viking-fm'

import { FileList } from './FileList'
import { FilePreview } from './FilePreview'
import { FileTree } from './FileTree'

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
  const [dialogPreviewFile, setDialogPreviewFile] =
    useState<VikingFsEntry | null>(null)

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
    setExpandedKeys((prev) => {
      const next = new Set(prev)
      for (const ancestor of getAncestorUris(normalized)) {
        next.add(ancestor)
      }
      return next
    })
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

  return (
    <LegacyPageShell
      title="Viking File Manager"
      description="三栏文件管理器：目录树 / 文件列表 / 文件预览。"
    >
      <div className="mb-3 flex items-center gap-2">
        <Button variant="outline" size="sm" onClick={handleGoParent}>
          <ArrowUp className="size-4" />
          返回父级
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => void handleRefresh()}
        >
          <RefreshCcw className="size-4" />
          刷新目录
        </Button>
      </div>

      <div className="h-[calc(100vh-14rem)] overflow-hidden rounded-xl border bg-card">
        <div className="grid h-full xl:grid-cols-[300px_minmax(0,1fr)_minmax(360px,0.85fr)]">
          <section className="flex min-h-0 flex-col border-r">
            <div className="border-b px-4 py-3 text-sm font-medium">目录树</div>
            <div className="min-h-0 flex-1">
              <FileTree
                currentUri={currentUri}
                expandedKeys={expandedKeys}
                onExpandedKeysChange={setExpandedKeys}
                onSelectDirectory={updateUri}
              />
            </div>
          </section>

          <section className="flex min-h-0 flex-col border-r">
            <div className="border-b px-4 py-3 text-sm font-medium">
              文件列表
              <span className="ml-2 text-xs text-muted-foreground">
                {listQuery.isLoading ? '加载中...' : `${entries.length} 项`}
              </span>
            </div>
            <div className="min-h-0 flex-1 overflow-auto">
              <FileList
                entries={entries}
                selectedFileUri={selectedFile?.uri || null}
                onOpenDirectory={updateUri}
                onOpenFile={(file) => setSelectedFile(file)}
                onPreviewFile={(file) => {
                  setSelectedFile(file)
                  setDialogPreviewFile(file)
                }}
              />
            </div>
          </section>

          <section className="flex min-h-0 flex-col">
            <div className="border-b px-4 py-3 text-sm font-medium">预览</div>
            <div className="min-h-0 flex-1">
              <FilePreview
                file={selectedFile}
                onClose={() => setSelectedFile(null)}
                showCloseButton={false}
              />
            </div>
          </section>
        </div>
      </div>

      <Dialog
        open={Boolean(dialogPreviewFile)}
        onOpenChange={(open) => {
          if (!open) {
            setDialogPreviewFile(null)
          }
        }}
      >
        <DialogContent
          showCloseButton={false}
          className="h-[80vh] w-[75vw] max-w-[75vw] overflow-hidden p-0 sm:max-w-[75vw]"
        >
          <DialogTitle className="sr-only">文件预览</DialogTitle>
          <FilePreview
            file={dialogPreviewFile}
            onClose={() => setDialogPreviewFile(null)}
          />
        </DialogContent>
      </Dialog>
    </LegacyPageShell>
  )
}
