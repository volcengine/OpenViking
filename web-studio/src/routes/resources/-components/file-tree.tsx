import { memo, useCallback, useRef } from 'react'
import { File } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { cn } from '#/lib/utils'

import { usePrefetchVikingFsList, useVikingFsList } from '../-hooks/viking-fm'
import type { VikingFsEntry } from '../-types/viking-fm'

interface FileTreeProps {
  currentUri: string
  selectedFileUri?: string | null
  expandedKeys: Set<string>
  onExpandedKeysChange: (next: Set<string>) => void
  onSelectDirectory: (entry: VikingFsEntry) => void
  onSelectFile?: (entry: VikingFsEntry) => void
}

interface TreeNodeProps {
  entry: VikingFsEntry
  level: number
  currentUri: string
  selectedFileUri?: string | null
  expandedKeys: Set<string>
  onExpandedKeysChange: (next: Set<string>) => void
  onSelectDirectory: (entry: VikingFsEntry) => void
  onSelectFile?: (entry: VikingFsEntry) => void
  prefetch?: (uri: string) => void
}

const FolderIcon = ({ isOpen }: { isOpen: boolean }) => (
  <svg
    className="size-4 shrink-0 text-foreground/70"
    viewBox="0 0 24 24"
    fill="currentColor"
    stroke="none"
  >
    {isOpen ? (
      <path d="M5 19a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h4l2 2h4a2 2 0 0 1 2 2v1M5 19h14a2 2 0 0 0 2-2v-5a2 2 0 0 0-2-2H9a2 2 0 0 0-2 2v5a2 2 0 0 1-2 2z" />
    ) : (
      <path d="M3 7v10a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-6l-2-2H5a2 2 0 0 0-2 2z" />
    )}
  </svg>
)

const ChevronIcon = ({ isOpen }: { isOpen: boolean }) => (
  <svg
    className={cn(
      'size-4 shrink-0 text-muted-foreground transition-transform',
      isOpen && 'rotate-90',
    )}
    viewBox="0 0 20 20"
    fill="currentColor"
  >
    <path
      fillRule="evenodd"
      d="M7.293 14.707a1 1 0 0 1 0-1.414L10.586 10 7.293 6.707a1 1 0 0 1 1.414-1.414l4 4a1 1 0 0 1 0 1.414l-4 4a1 1 0 0 1-1.414 0z"
      clipRule="evenodd"
    />
  </svg>
)

const LIST_OPTS = {
  output: 'agent' as const,
  showAllHidden: true,
  nodeLimit: 200,
}

function isSessionSubtree(parentUri: string): boolean {
  // Children of viking://session and anything nested inside it sort by
  // modTime DESC (most recent first) instead of name.
  return parentUri.startsWith('viking://session/')
}

function sortEntries(
  entries: VikingFsEntry[],
  parentUri: string,
): VikingFsEntry[] {
  const byModTime = isSessionSubtree(parentUri)
  return [...entries].sort((a, b) => {
    if (a.isDir !== b.isDir) return a.isDir ? -1 : 1
    if (byModTime) {
      const at = a.modTimestamp ?? 0
      const bt = b.modTimestamp ?? 0
      if (at !== bt) return bt - at
    }
    return a.name.localeCompare(b.name)
  })
}

function TreeNode({
  entry,
  level,
  currentUri,
  selectedFileUri,
  expandedKeys,
  onExpandedKeysChange,
  onSelectDirectory,
  onSelectFile,
  prefetch,
}: TreeNodeProps) {
  const { t } = useTranslation('resources')
  const isOpen = expandedKeys.has(entry.uri)
  const isDirSelected =
    entry.isDir && currentUri === entry.uri && !selectedFileUri
  const isFileSelected = !entry.isDir && selectedFileUri === entry.uri

  const entryRef = useRef(entry)
  entryRef.current = entry

  const { data } = useVikingFsList(entry.uri, LIST_OPTS, entry.isDir)
  const children: VikingFsEntry[] = entry.isDir
    ? sortEntries(data?.entries ?? [], entry.uri)
    : []

  const handleToggle = useCallback(() => {
    const next = new Set(expandedKeys)
    if (isOpen) {
      next.delete(entry.uri)
    } else {
      for (const key of next) {
        if (key !== entry.uri && key.startsWith(entry.uri)) {
          next.delete(key)
        }
      }
      next.add(entry.uri)
    }
    onExpandedKeysChange(next)
  }, [expandedKeys, isOpen, entry.uri, onExpandedKeysChange])

  const handleSelect = useCallback(() => {
    if (entryRef.current.isDir) {
      onSelectDirectory(entryRef.current)
    } else {
      onSelectFile?.(entryRef.current)
    }
  }, [onSelectDirectory, onSelectFile])

  const handleMouseEnter = useCallback(() => {
    if (entry.isDir && !isOpen && prefetch) prefetch(entry.uri)
  }, [entry.isDir, entry.uri, isOpen, prefetch])

  if (!entry.isDir) {
    return (
      <div
        className="relative text-foreground"
        style={{ marginLeft: `${level * 16}px` }}
      >
        {level > 0 && (
          <span className="absolute -left-2 top-1/2 h-3 w-2 -translate-y-1/2 rounded-bl-md border-b border-l border-border" />
        )}
        <div
          className={cn(
            'flex cursor-pointer items-center gap-2 rounded-md px-2 py-1 transition-colors md:py-1.5',
            isFileSelected ? 'bg-muted text-foreground' : 'hover:bg-muted/50',
          )}
          onClick={handleSelect}
        >
          <span aria-hidden className="inline-flex size-4 shrink-0" />
          <File className="size-4 shrink-0 text-muted-foreground" />
          <span className="min-w-0 truncate text-xs md:text-sm">{entry.name}</span>
        </div>
      </div>
    )
  }

  return (
    <div className="relative text-foreground">
      <div className="relative" style={{ marginLeft: `${level * 16}px` }}>
        {level > 0 && (
          <span className="absolute -left-2 top-1/2 h-3 w-2 -translate-y-1/2 rounded-bl-md border-b border-l border-border" />
        )}
        <div
          className={cn(
            'flex cursor-pointer items-center gap-2 rounded-md px-2 py-1 transition-colors md:py-1.5',
            isDirSelected ? 'bg-muted text-foreground' : 'hover:bg-muted/50',
          )}
          onClick={handleSelect}
          onMouseEnter={handleMouseEnter}
        >
          {!data || children.length > 0 ? (
            <button
              type="button"
              className="inline-flex shrink-0"
              onClick={(e) => {
                e.stopPropagation()
                handleToggle()
              }}
              aria-label={
                isOpen ? t('fileTree.collapse') : t('fileTree.expand')
              }
            >
              <ChevronIcon isOpen={isOpen} />
            </button>
          ) : (
            <span aria-hidden className="inline-flex size-4 shrink-0" />
          )}
          <FolderIcon isOpen={isOpen} />
          <span className="min-w-0 truncate text-xs md:text-sm">{entry.name}</span>
        </div>
      </div>
      <div
        className={cn(
          'grid transition-[grid-template-rows] duration-300 ease-in-out',
          isOpen ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]',
        )}
      >
        <div className="overflow-hidden">
          {isOpen && !data && (
            <div
              className="px-2 py-1 text-xs text-muted-foreground"
              style={{ marginLeft: `${(level + 1) * 16}px` }}
            >
              {t('fileTree.loading')}
            </div>
          )}
          {isOpen &&
            children.map((child) => (
              <TreeNodeMemo
                key={child.uri}
                entry={child}
                level={level + 1}
                currentUri={currentUri}
                selectedFileUri={selectedFileUri}
                expandedKeys={expandedKeys}
                onExpandedKeysChange={onExpandedKeysChange}
                onSelectDirectory={onSelectDirectory}
                onSelectFile={onSelectFile}
                prefetch={prefetch}
              />
            ))}
        </div>
      </div>
    </div>
  )
}

const TreeNodeMemo = memo(TreeNode)

const ROOT_ENTRY: VikingFsEntry = {
  uri: 'viking://',
  name: 'viking://',
  isDir: true,
  size: '',
  sizeBytes: null,
  modTime: '',
  modTimestamp: null,
  abstract: '',
  overview: '',
}

export function FileTree({
  currentUri,
  selectedFileUri,
  expandedKeys,
  onExpandedKeysChange,
  onSelectDirectory,
  onSelectFile,
}: FileTreeProps) {
  const { prefetch } = usePrefetchVikingFsList()

  return (
    <div className="h-full overflow-auto font-mono">
      <div className="min-w-0 p-2">
        <TreeNodeMemo
          entry={ROOT_ENTRY}
          level={0}
          currentUri={currentUri}
          selectedFileUri={selectedFileUri}
          expandedKeys={expandedKeys}
          onExpandedKeysChange={onExpandedKeysChange}
          onSelectDirectory={onSelectDirectory}
          onSelectFile={onSelectFile}
          prefetch={prefetch}
        />
      </div>
    </div>
  )
}
