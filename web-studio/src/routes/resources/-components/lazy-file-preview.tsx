import { lazy, Suspense } from 'react'
import { Loader2 } from 'lucide-react'

import type { VikingFsEntry } from '../-types/viking-fm'

const FilePreview = lazy(() =>
  import('./file-preview').then((module) => ({
    default: module.FilePreview,
  })),
)

export function LazyFilePreview({
  file,
  hideDirectoryHeader,
  onClose,
  showCloseButton,
}: {
  file: VikingFsEntry | null
  hideDirectoryHeader?: boolean
  onClose: () => void
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
        file={file}
        hideDirectoryHeader={hideDirectoryHeader}
        onClose={onClose}
        showCloseButton={showCloseButton}
      />
    </Suspense>
  )
}
