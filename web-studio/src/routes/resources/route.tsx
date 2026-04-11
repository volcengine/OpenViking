import { createFileRoute } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'

export const Route = createFileRoute('/resources')({
  component: ResourcesRoute,
})

function ResourcesRoute() {
  const { t } = useTranslation('resources')

  return (
    <div className="flex items-center justify-center w-full h-full">
      <p>{t('page.placeholder')}</p>
    </div>
  )
}