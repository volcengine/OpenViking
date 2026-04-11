import { createFileRoute } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'

export const Route = createFileRoute('/admin')({
  component: AdminRoute,
})

function AdminRoute() {
  const { t } = useTranslation('admin')

  return (
    <div className="flex items-center justify-center w-full h-full">
      <p>{t('page.placeholder')}</p>
    </div>
  )
}