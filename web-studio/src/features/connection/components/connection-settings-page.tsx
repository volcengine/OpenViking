import { ConnectionProfilesList } from '#/features/connection/components/connection-profiles'
import { useTranslation } from 'react-i18next'

// Local connection configuration is also the recovery page for an unavailable server.
export function ConnectionSettingsPage() {
  const { t } = useTranslation('studio')
  return (
    <div className="flex w-full min-w-0 flex-col gap-5">
      <h1 className="text-2xl font-semibold">
        {t('appShell:footer.connection')}
      </h1>
      <ConnectionProfilesList />
    </div>
  )
}
