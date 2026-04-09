import { useMemo, useState, useCallback } from 'react'
import { ArrowDown, ArrowUp, File, Folder } from 'lucide-react'

import { Badge } from '#/components/ui/badge'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '#/components/ui/table'
import { formatSize } from '#/lib/viking-fm'
import type { VikingFsEntry } from '#/lib/viking-fm'
import { cn } from '#/lib/utils'

type SortKey = 'name' | 'size' | 'modTime'
type SortDirection = 'asc' | 'desc'

interface FileListProps {
  entries: Array<VikingFsEntry>
  selectedFileUri: string | null
  onOpenDirectory: (uri: string) => void
  onOpenFile: (file: VikingFsEntry) => void
  onPreviewFile: (file: VikingFsEntry) => void
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
  onPreviewFile,
}: FileListProps) {
  const [sortKey, setSortKey] = useState<SortKey>('name')
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc')

  const sortedEntries = useMemo(
    () =>
      [...entries].sort((left, right) =>
        compareEntries(left, right, sortKey, sortDirection),
      ),
    [entries, sortKey, sortDirection],
  )

  const toggleSort = useCallback((nextKey: SortKey) => {
    setSortKey((prev) => {
      if (prev === nextKey) {
        setSortDirection((d) => (d === 'asc' ? 'desc' : 'asc'))
        return prev
      }
      setSortDirection('asc')
      return nextKey
    })
  }, [])

  const SortIcon = sortDirection === 'asc' ? ArrowUp : ArrowDown

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>
            <button
              className="inline-flex items-center gap-1"
              type="button"
              onClick={() => toggleSort('name')}
            >
              名称
              {sortKey === 'name' ? <SortIcon className="size-3" /> : null}
            </button>
          </TableHead>
          <TableHead>
            <button
              className="inline-flex items-center gap-1"
              type="button"
              onClick={() => toggleSort('size')}
            >
              大小
              {sortKey === 'size' ? <SortIcon className="size-3" /> : null}
            </button>
          </TableHead>
          <TableHead>
            <button
              className="inline-flex items-center gap-1"
              type="button"
              onClick={() => toggleSort('modTime')}
            >
              修改时间
              {sortKey === 'modTime' ? <SortIcon className="size-3" /> : null}
            </button>
          </TableHead>
          <TableHead className="text-right">操作</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {!sortedEntries.length ? (
          <TableRow>
            <TableCell className="text-muted-foreground" colSpan={4}>
              当前目录为空
            </TableCell>
          </TableRow>
        ) : null}

        {sortedEntries.map((entry) => {
          const isSelected = selectedFileUri === entry.uri
          const tags = parseTags(entry.tags)

          return (
            <TableRow key={entry.uri} className={cn(isSelected && 'bg-muted')}>
              <TableCell>
                <button
                  className="inline-flex w-full items-center gap-2 text-left"
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
                    <Folder className="size-4 text-amber-500" />
                  ) : (
                    <File className="size-4 text-muted-foreground" />
                  )}
                  <span className="truncate">{entry.name}</span>
                  {tags.length ? (
                    <span className="ml-2 flex flex-wrap items-center gap-1">
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
                </button>
              </TableCell>
              <TableCell>
                {entry.isDir ? '-' : formatSize(entry.sizeBytes ?? entry.size)}
              </TableCell>
              <TableCell>{entry.modTime || '-'}</TableCell>
              <TableCell className="text-right">
                {!entry.isDir ? (
                  <button
                    className="rounded-md border px-2 py-1 text-xs hover:bg-muted"
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation()
                      onPreviewFile(entry)
                    }}
                  >
                    预览
                  </button>
                ) : (
                  <span className="text-muted-foreground">-</span>
                )}
              </TableCell>
            </TableRow>
          )
        })}
      </TableBody>
    </Table>
  )
}
