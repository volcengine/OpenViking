import { createFileRoute } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'

export const Route = createFileRoute('/operations')({
  component: OperationsRoute,
})

function OperationsRoute() {
  const { t } = useTranslation('operations')

  return (
    <div className="flex items-center justify-center w-full h-full">
      <p>{t('page.placeholder')}</p>
    </div>
  )
}