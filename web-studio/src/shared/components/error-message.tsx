import { useTranslation } from 'react-i18next'
import { getErrorMessage, getLocalizedErrorMessage } from '#/shared/lib/error'
import { cn } from '#/shared/lib/utils'

export function ErrorMessage({
  error,
  className,
}: {
  error: string | Error
  className?: string
}) {
  const { t } = useTranslation('studio')
  return (
    <div role="alert" className={cn('text-sm text-destructive', className)}>
      <p>
        {typeof error === 'string' ? error : getLocalizedErrorMessage(error)}
      </p>
      {error instanceof Error && (
        <details className="mt-2">
          <summary className="cursor-pointer">{t('errors.details')}</summary>
          <pre className="mt-2 whitespace-pre-wrap break-all font-mono text-xs">
            {getErrorMessage(error)}
          </pre>
        </details>
      )}
    </div>
  )
}
