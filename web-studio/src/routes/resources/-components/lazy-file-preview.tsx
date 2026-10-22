import { lazy, Suspense } from 'react'
import { Loader2 } from 'lucide-react'

import type { VikingFsEntry } from '../-types/viking-fm'

const FilePreview = lazy(() =>
  import('./file-preview').then((module) => ({
    default: module.FilePreview,
  })),
)

export function LazyFilePreview({
  readOnly,
  defaultReadLimit,
  file,
  hideDirectoryHeader,
  onClose,
  onNavigate,
  showCloseButton,
}: {
  readOnly?: boolean
  defaultReadLimit?: number
  file: VikingFsEntry | null
  hideDirectoryHeader?: boolean
  onClose: () => void
  onNavigate?: (uri: string) => void
  showCloseButton?: boolean
}) {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-0 flex-1 items-center justify-center">
          <Loader2 className="size-4 animate-spin text-muted-foreground" />
        </div>
      }
    >
      <FilePreview
        readOnly={readOnly}
        defaultReadLimit={defaultReadLimit}
        file={file}
        hideDirectoryHeader={hideDirectoryHeader}
        onClose={onClose}
        onNavigate={onNavigate}
        showCloseButton={showCloseButton}
      />
    </Suspense>
  )
}
