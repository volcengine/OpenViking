import { Loader2 } from 'lucide-react'
import { lazy, Suspense } from 'react'

import type { VikingFsEntry } from '#/shared/types/viking-fs'

const FilePreview = lazy(() =>
  import('#/features/workbench/resources/components/file-preview').then(
    (module) => ({
      default: module.FilePreview,
    }),
  ),
)

export function LazyFilePreview({
  onDirtyChange,
  onSaved,
  readOnly,
  defaultReadLimit,
  file,
  onClose,
  onNavigate,
  showCloseButton,
}: {
  onSaved?: (uri: string) => void
  onDirtyChange?: (dirty: boolean) => void
  readOnly?: boolean
  defaultReadLimit?: number
  file: VikingFsEntry | null
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
        onDirtyChange={onDirtyChange}
        onSaved={onSaved}
        readOnly={readOnly}
        defaultReadLimit={defaultReadLimit}
        file={file}
        onClose={onClose}
        onNavigate={onNavigate}
        showCloseButton={showCloseButton}
      />
    </Suspense>
  )
}
