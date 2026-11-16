import { cn } from '#/shared/lib/utils'
import { Loader2Icon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

function Spinner({ className, ...props }: React.ComponentProps<'svg'>) {
  const { t } = useTranslation('common')
  return (
    <Loader2Icon
      role="status"
      aria-label={t('ui.loading')}
      className={cn('size-4 animate-spin', className)}
      {...props}
    />
  )
}

export { Spinner }
