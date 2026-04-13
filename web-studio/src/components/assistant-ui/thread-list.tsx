import type { RefObject } from 'react'
import {
  ThreadListPrimitive,
  ThreadListItemPrimitive,
} from '@assistant-ui/react'
import { MessageSquareIcon, PlusIcon, SearchIcon, TrashIcon, XIcon } from 'lucide-react'

import { cn } from '#/lib/utils'
import { Skeleton } from '#/components/ui/skeleton'
import { useSessionList } from '#/routes/sessions/-hooks/use-sessions'

interface ThreadListProps {
  searchQuery: string
  onSearchChange: (query: string) => void
  searchInputRef: RefObject<HTMLInputElement | null>
}

export function ThreadList({ searchQuery, onSearchChange, searchInputRef }: ThreadListProps) {
  const { isLoading, isError } = useSessionList()

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-12 items-center justify-between border-b border-border px-4">
        <h2 className="text-[13px] font-semibold tracking-wide uppercase text-muted-foreground">
          Sessions
        </h2>
        <ThreadListPrimitive.New asChild>
          <button
            type="button"
            className={cn(
              'inline-flex size-7 items-center justify-center rounded-md',
              'text-muted-foreground transition-colors',
              'hover:bg-accent hover:text-accent-foreground',
            )}
          >
            <PlusIcon className="size-4" />
          </button>
        </ThreadListPrimitive.New>
      </div>

      {/* Search */}
      <div className="px-2 py-2">
        <div className="relative">
          <SearchIcon className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <input
            ref={searchInputRef}
            type="text"
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder="Search sessions..."
            className={cn(
              'w-full rounded-md bg-muted/50 py-1.5 pl-7 pr-7 text-xs',
              'placeholder:text-muted-foreground/60',
              'focus:bg-muted focus:outline-none focus:ring-1 focus:ring-ring',
              'transition-colors',
            )}
            onKeyDown={(e) => {
              if (e.key === 'Escape') {
                onSearchChange('')
                ;(e.target as HTMLInputElement).blur()
              }
            }}
          />
          {searchQuery && (
            <button
              type="button"
              onClick={() => {
                onSearchChange('')
                searchInputRef.current?.focus()
              }}
              className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:text-foreground"
            >
              <XIcon className="size-3" />
            </button>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        {isLoading ? (
          <ThreadListSkeleton />
        ) : isError ? (
          <div className="px-4 py-6 text-center text-xs text-destructive">
            Failed to load sessions
          </div>
        ) : (
          <ThreadListPrimitive.Items components={{ ThreadListItem }} />
        )}
      </div>
    </div>
  )
}

function ThreadListSkeleton() {
  return (
    <div className="space-y-1 px-2">
      {Array.from({ length: 5 }).map((_, i) => (
        <div
          key={i}
          className="flex items-center gap-2.5 px-2.5 py-2"
        >
          <Skeleton className="size-3.5 rounded" />
          <Skeleton className="h-4 flex-1" style={{ width: `${60 + Math.random() * 30}%` }} />
        </div>
      ))}
    </div>
  )
}

function ThreadListItem() {
  return (
    <ThreadListItemPrimitive.Root className="group px-2">
      <ThreadListItemPrimitive.Trigger
        className={cn(
          'flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-sm',
          'transition-colors',
          'text-muted-foreground hover:bg-accent hover:text-accent-foreground',
          'group-data-[active]:bg-accent group-data-[active]:text-foreground group-data-[active]:font-medium',
        )}
      >
        <MessageSquareIcon className="size-3.5 shrink-0 opacity-60" />
        <span className="min-w-0 flex-1 truncate">
          <ThreadListItemPrimitive.Title fallback="New Session" />
        </span>
        <ThreadListItemPrimitive.Delete asChild>
          <span
            role="button"
            tabIndex={0}
            className={cn(
              'shrink-0 rounded p-0.5 transition-opacity',
              'opacity-0 group-hover:opacity-100',
              'text-muted-foreground hover:text-destructive',
            )}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => { if (e.key === 'Enter') e.stopPropagation() }}
          >
            <TrashIcon className="size-3.5" />
          </span>
        </ThreadListItemPrimitive.Delete>
      </ThreadListItemPrimitive.Trigger>
    </ThreadListItemPrimitive.Root>
  )
}
