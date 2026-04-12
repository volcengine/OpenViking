import { memo, useCallback } from 'react'

import { fileNameFromUri } from '../-lib/normalize'
import { usePrefetchVikingFsList, useVikingFsList } from '../-hooks/viking-fm'
import type { VikingFsEntry } from '../-types/viking-fm'

interface FileTreeProps {
  currentUri: string
  expandedKeys: Set<string>
  onExpandedKeysChange: (next: Set<string>) => void
  onSelectDirectory: (uri: string) => void
}

interface FileTreeItem {
  uri: string
  name: string
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
  <svg className="mr-2 size-5 shrink-0 text-gray-700 dark:text-gray-300" viewBox="0 0 24 24" fill="currentColor" stroke="none">
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
  const isOpen = expandedKeys.has(item.uri)
  const isSelected = currentUri === item.uri

  const { data } = useVikingFsList(item.uri, LIST_OPTS)
  const children: FileTreeItem[] = (data?.entries ?? [])
    .filter((e: VikingFsEntry) => e.isDir)
    .map((e: VikingFsEntry) => ({ uri: e.uri, name: e.name }))
    .sort((a, b) => a.name.localeCompare(b.name))

  const handleToggle = useCallback(() => {
    const next = new Set(expandedKeys)
    isOpen ? next.delete(item.uri) : next.add(item.uri)
    onExpandedKeysChange(next)
  }, [expandedKeys, isOpen, item.uri, onExpandedKeysChange])

  const handleSelect = useCallback(() => {
    onSelectDirectory(item.uri)
  }, [item.uri, onSelectDirectory])

  const handleMouseEnter = useCallback(() => {
    if (!isOpen && prefetch) prefetch(item.uri)
  }, [isOpen, prefetch, item.uri])

  return (
    <div className="relative text-gray-700 dark:text-gray-300">
      <div className="relative" style={{ marginLeft: `${level * 16}px` }}>
        {level > 0 && <span className="absolute -left-2 top-1/2 h-3 w-2 -translate-y-1/2 rounded-bl-md border-b border-l border-gray-300 dark:border-gray-700" />}
        <div className={`flex cursor-pointer items-center rounded-md px-2 py-1.5 transition-colors ${isSelected ? 'bg-gray-200 dark:bg-gray-700 text-foreground' : 'hover:bg-gray-100 dark:hover:bg-gray-800'}`}
          onClick={handleSelect} onMouseEnter={handleMouseEnter}>
          <div className="flex min-w-0 flex-grow items-center">
            <button type="button" className="inline-flex" onClick={(e) => { e.stopPropagation(); handleToggle() }} aria-label={isOpen ? '收起' : '展开'}>
              <ChevronIcon isOpen={isOpen} />
            </button>
            <div className="ml-1 flex min-w-0 items-center">
              <FolderIcon isOpen={isOpen} />
              <span className="ml-1.5 truncate text-sm">{item.name}</span>
            </div>
          </div>
        </div>
      </div>
      <div className={`grid transition-[grid-template-rows] duration-300 ease-in-out ${isOpen ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'}`}>
        <div className="overflow-hidden">
          {isOpen && !data && <div className="px-2 py-1 text-xs text-muted-foreground" style={{ marginLeft: `${(level + 1) * 16}px` }}>加载中...</div>}
          {isOpen && children.map((child) => (
            <TreeNodeMemo key={child.uri} item={child} level={level + 1} currentUri={currentUri} expandedKeys={expandedKeys} onExpandedKeysChange={onExpandedKeysChange} onSelectDirectory={onSelectDirectory} prefetch={prefetch} />
          ))}
        </div>
      </div>
    </div>
  )
}

const TreeNodeMemo = memo(TreeNode)

export function FileTree({ currentUri, expandedKeys, onExpandedKeysChange, onSelectDirectory }: FileTreeProps) {
  const { prefetch } = usePrefetchVikingFsList()

  return (
    <div className="h-full overflow-auto font-mono">
      <div className="min-w-0 p-2">
        <TreeNodeMemo item={{ uri: 'viking://', name: fileNameFromUri('viking://') }}
          level={0} currentUri={currentUri} expandedKeys={expandedKeys} onExpandedKeysChange={onExpandedKeysChange}
          onSelectDirectory={onSelectDirectory} prefetch={prefetch} />
      </div>
    </div>
  )
}
