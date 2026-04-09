import { memo, useCallback } from 'react'

import { fileNameFromUri, usePrefetchVikingFsList, useVikingFsList } from '#/lib/viking-fm'
import type { VikingFsEntry } from '#/lib/viking-fm'

interface FileTreeProps {
  currentUri: string
  expandedKeys: Set<string>
  onExpandedKeysChange: (next: Set<string>) => void
  onSelectDirectory: (uri: string) => void
}

interface FileTreeItem {
  uri: string
  name: string
  type: 'folder' | 'file'
}

interface TreeNodeProps {
  item: FileTreeItem
  level: number
  currentUri: string
  expandedKeys: Set<string>
  onExpandedKeysChange: (next: Set<string>) => void
  onSelectDirectory: (uri: string) => void
  prefetch?: (uri: string) => void
}

const FolderIcon = ({ isOpen }: { isOpen: boolean }) => (
  <svg className="mr-2 size-5 shrink-0 text-yellow-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    {isOpen ? (
      <path d="M5 19a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h4l2 2h4a2 2 0 0 1 2 2v1M5 19h14a2 2 0 0 0 2-2v-5a2 2 0 0 0-2-2H9a2 2 0 0 0-2 2v5a2 2 0 0 1-2 2z" />
    ) : (
      <path d="M3 7v10a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-6l-2-2H5a2 2 0 0 0-2 2z" />
    )}
  </svg>
)

const ChevronIcon = ({ isOpen }: { isOpen: boolean }) => (
  <svg className={`size-4 shrink-0 text-gray-500 transition-transform dark:text-gray-400 ${isOpen ? 'rotate-90' : ''}`} viewBox="0 0 20 20" fill="currentColor">
    <path fillRule="evenodd" d="M7.293 14.707a1 1 0 0 1 0-1.414L10.586 10 7.293 6.707a1 1 0 0 1 1.414-1.414l4 4a1 1 0 0 1 0 1.414l-4 4a1 1 0 0 1-1.414 0z" clipRule="evenodd" />
  </svg>
)

const FileIcon = () => (
  <svg className="mr-2 size-5 shrink-0 text-gray-500 dark:text-gray-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M7 21h10a2 2 0 0 0 2-2V9.414a1 1 0 0 0-.293-.707l-5.414-5.414A1 1 0 0 0 12.586 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2z" />
  </svg>
)

const LIST_OPTS = { output: 'agent' as const, showAllHidden: true, nodeLimit: 200 }

function TreeNode({
  item,
  level,
  currentUri,
  expandedKeys,
  onExpandedKeysChange,
  onSelectDirectory,
  prefetch,
}: TreeNodeProps) {
  const isFolder = item.type === 'folder'
  const isOpen = expandedKeys.has(item.uri)
  const isSelected = currentUri === item.uri

  const { data } = useVikingFsList(item.uri, LIST_OPTS)
  const folderChildren: FileTreeItem[] = (data?.entries ?? [])
    .filter((e: VikingFsEntry) => e.isDir)
    .map((e: VikingFsEntry) => ({ uri: e.uri, name: e.name, type: 'folder' as const }))

  const handleToggle = useCallback(() => {
    if (!isFolder) return
    const next = new Set(expandedKeys)
    isOpen ? next.delete(item.uri) : next.add(item.uri)
    onExpandedKeysChange(next)
  }, [expandedKeys, isFolder, isOpen, item.uri, onExpandedKeysChange])

  const handleSelect = useCallback(() => {
    if (isFolder) onSelectDirectory(item.uri)
  }, [isFolder, item.uri, onSelectDirectory])

  const handleMouseEnter = useCallback(() => {
    if (isFolder && !isOpen && prefetch) prefetch(item.uri)
  }, [isFolder, isOpen, prefetch, item.uri])

  return (
    <div className="relative text-gray-700 dark:text-gray-300">
      <div className="relative" style={{ marginLeft: `${level * 16}px` }}>
        {level > 0 && <span className="absolute -left-2 top-1/2 h-3 w-2 -translate-y-1/2 rounded-bl-md border-b border-l border-gray-300 dark:border-gray-700" />}
        <div className={`flex cursor-pointer items-center rounded-md px-2 py-1.5 transition-colors ${isSelected ? 'bg-blue-100 text-blue-700 dark:bg-blue-500/20 dark:text-white' : 'hover:bg-gray-100 dark:hover:bg-gray-800'}`}
          onClick={handleSelect} onMouseEnter={handleMouseEnter}>
          <div className="flex flex-grow items-center">
            <button type="button" className="inline-flex" onClick={(e) => { e.stopPropagation(); handleToggle() }} aria-label={isOpen ? '收起' : '展开'}>
              {isFolder ? <ChevronIcon isOpen={isOpen} /> : <div className="w-4 shrink-0" />}
            </button>
            <div className="ml-1 flex min-w-0 items-center">
              {isFolder ? <FolderIcon isOpen={isOpen} /> : <FileIcon />}
              <span className="ml-1.5 truncate text-sm">{item.name}</span>
            </div>
          </div>
        </div>
      </div>
      <div className={`relative overflow-hidden transition-all duration-300 ease-in-out ${isOpen ? 'max-h-[1000px]' : 'max-h-0'}`}>
        {isOpen && !data && <div className="px-2 py-1 text-xs text-muted-foreground" style={{ marginLeft: `${(level + 1) * 16}px` }}>加载中...</div>}
        {isOpen && folderChildren.map((child) => (
          <TreeNodeMemo key={child.uri} item={child} level={level + 1} currentUri={currentUri} expandedKeys={expandedKeys} onExpandedKeysChange={onExpandedKeysChange} onSelectDirectory={onSelectDirectory} prefetch={prefetch} />
        ))}
      </div>
    </div>
  )
}

const TreeNodeMemo = memo(TreeNode)

export function FileTree({ currentUri, expandedKeys, onExpandedKeysChange, onSelectDirectory }: FileTreeProps) {
  const { prefetch } = usePrefetchVikingFsList()

  return (
    <div className="h-full overflow-auto font-mono">
      <div className="min-w-max p-2">
        <TreeNodeMemo item={{ uri: 'viking://', name: fileNameFromUri('viking://'), type: 'folder' }}
          level={0} currentUri={currentUri} expandedKeys={expandedKeys} onExpandedKeysChange={onExpandedKeysChange}
          onSelectDirectory={onSelectDirectory} prefetch={prefetch} />
      </div>
    </div>
  )
}
