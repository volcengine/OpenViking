import { createFileRoute } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'

export const Route = createFileRoute('/sessions')({
  component: SessionsRoute,
})

function SessionsRoute() {
  const { t } = useTranslation('sessions')

  return (
    <div className="flex items-center justify-center w-full h-full">
      <p>{t('page.placeholder')}</p>
    </div>
  )
}