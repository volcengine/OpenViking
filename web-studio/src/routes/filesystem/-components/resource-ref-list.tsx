import { FileTextIcon, FolderIcon, Loader2Icon } from 'lucide-react'

import { cn } from '#/lib/utils'
import { fileNameFromUri } from '#/routes/resources/-lib/normalize'

import type { ResourceOpenHandler, ResourceRef } from '../-lib/types'

export function ResourceRefList({
  className,
  refs,
  onOpenResource,
  openingUri,
}: {
  className?: string
  refs: ResourceRef[]
  onOpenResource: ResourceOpenHandler
  openingUri?: string | null
}) {
  return (
    <div className={cn('grid gap-1.5', className)}>
      {refs.map((ref) => (
        <button
          key={`${ref.uri}-${ref.meta ?? ''}`}
          type="button"
          className="flex min-w-0 items-center gap-2 rounded-md border bg-background px-2 py-1.5 text-left transition-colors hover:border-primary/50 hover:bg-primary/5"
          onClick={() => void onOpenResource(ref.uri)}
        >
          {openingUri === ref.uri ? (
            <Loader2Icon className="size-3.5 shrink-0 animate-spin text-primary" />
          ) : ref.uri.endsWith('/') ? (
            <FolderIcon className="size-3.5 shrink-0 text-muted-foreground" />
          ) : (
            <FileTextIcon className="size-3.5 shrink-0 text-muted-foreground" />
          )}
          <span className="min-w-0 flex-1 truncate font-mono text-[11px]">
            {ref.label || fileNameFromUri(ref.uri) || ref.uri}
          </span>
          {ref.meta ? (
            <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
              {ref.meta}
            </span>
          ) : null}
        </button>
      ))}
    </div>
  )
}
