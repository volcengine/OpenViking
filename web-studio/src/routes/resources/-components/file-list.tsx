import { useMemo } from 'react'
import { File, Folder } from 'lucide-react'

import { Badge } from '#/components/ui/badge'
import { cn } from '#/lib/utils'

import { formatSize } from '../-lib/normalize'
import type { VikingFsEntry } from '../-types/viking-fm'

type SortKey = 'name' | 'size' | 'modTime'
type SortDirection = 'asc' | 'desc'

interface FileListProps {
  entries: Array<VikingFsEntry>
  selectedFileUri: string | null
  onOpenDirectory: (uri: string) => void
  onOpenFile: (file: VikingFsEntry) => void
}

function parseTags(tags?: string): Array<string> {
  if (!tags) {
    return []
  }

  return tags
    .split(/[;,]+|\s+/)
    .map((tag) => tag.trim())
    .filter(Boolean)
}

function compareEntries(
  left: VikingFsEntry,
  right: VikingFsEntry,
  sortKey: SortKey,
  sortDirection: SortDirection,
): number {
  if (left.isDir !== right.isDir) {
    return left.isDir ? -1 : 1
  }

  let comparison = 0
  if (sortKey === 'name') {
    comparison = left.name.localeCompare(right.name)
  } else if (sortKey === 'size') {
    comparison = (left.sizeBytes ?? -1) - (right.sizeBytes ?? -1)
  } else {
    comparison = (left.modTimestamp ?? 0) - (right.modTimestamp ?? 0)
  }

  return sortDirection === 'asc' ? comparison : -comparison
}

export function FileList({
  entries,
  selectedFileUri,
  onOpenDirectory,
  onOpenFile,
}: FileListProps) {
  const sortKey: SortKey = 'name'
  const sortDirection: SortDirection = 'asc'

  const sortedEntries = useMemo(
    () =>
      [...entries].sort((left, right) =>
        compareEntries(left, right, sortKey, sortDirection),
      ),
    [entries, sortKey, sortDirection],
  )

  return (
    <div>
      {!sortedEntries.length ? (
        <div className="px-4 py-8 text-center text-sm text-muted-foreground">
          当前目录为空
        </div>
      ) : null}

      {sortedEntries.map((entry) => {
        const isSelected = selectedFileUri === entry.uri
        const tags = parseTags(entry.tags)

        return (
          <button
            key={entry.uri}
            className={cn(
              'flex w-full items-center gap-2 border-b px-4 py-2 text-left text-sm transition-colors hover:bg-muted/50',
              isSelected && 'bg-muted',
            )}
            type="button"
            onClick={() => {
              if (entry.isDir) {
                onOpenDirectory(entry.uri)
                return
              }
              onOpenFile(entry)
            }}
          >
            {entry.isDir ? (
              <Folder className="size-4 shrink-0 fill-gray-700 text-gray-700 dark:fill-gray-300 dark:text-gray-300" />
            ) : (
              <File className="size-4 shrink-0 text-muted-foreground" />
            )}
            <span className="min-w-0 truncate">{entry.name}</span>
            {tags.length ? (
              <span className="flex shrink-0 flex-wrap items-center gap-1">
                {tags.map((tag) => (
                  <Badge
                    key={`${entry.uri}-${tag}`}
                    variant="secondary"
                    className="bg-muted/60 text-muted-foreground"
                  >
                    {tag}
                  </Badge>
                ))}
              </span>
            ) : null}
            <span className="ml-auto flex shrink-0 items-center gap-4 text-xs text-muted-foreground">
              <span>{entry.isDir ? '-' : formatSize(entry.sizeBytes ?? entry.size)}</span>
              <span className="w-20 text-right">{entry.modTime || '-'}</span>
            </span>
          </button>
        )
      })}
    </div>
  )
}
