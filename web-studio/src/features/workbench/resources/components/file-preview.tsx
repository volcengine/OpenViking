import type { ComponentProps } from 'react'
import { XIcon } from 'lucide-react'
import { SharedFilePreview } from '#/features/workbench/filesystem/components/shared-file-preview'
import { Button } from '#/shared/ui/button'
import { useTranslation } from 'react-i18next'
import type { LazyFilePreview } from './lazy-file-preview'

export {
  parseJsonlRecords,
  normalizeJsonlDisplayText,
  getJsonlMessage,
  hasJsonlToolPart,
} from './structured-file-content'
const noop = () => {}
export function FilePreview({
  file,
  onClose,
  onNavigate,
  onDirtyChange = noop,
  onSaved = noop,
  readOnly,
  defaultReadLimit,
  showCloseButton = true,
}: ComponentProps<typeof LazyFilePreview>) {
  const { t } = useTranslation('resources')
  if (!file)
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
        {t('filePreview.emptyPrompt')}
      </div>
    )
  return (
    <SharedFilePreview
      file={file}
      readOnly={readOnly}
      defaultReadLimit={defaultReadLimit}
      onNavigate={onNavigate}
      onDirtyChange={onDirtyChange}
      onSaved={onSaved}
      extraActions={
        showCloseButton ? (
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={t('common:close')}
            onClick={onClose}
          >
            <XIcon className="size-4" />
          </Button>
        ) : undefined
      }
    />
  )
}
