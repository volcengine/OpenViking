import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { Button } from '#/shared/ui/button'
import { Link } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'

export function RootSetupGuide({ onCreate }: { onCreate: () => void }) {
  const { t } = useTranslation('onboarding')
  const {
    connection,
    connectionRole,
    serverMode,
    profiles = [],
    activeProfileId,
  } = useAppConnection()
  const workbench = profiles.find(
    (profile) => profile.id === activeProfileId,
  )?.workbenchIdentity
  if (
    connectionRole !== 'root' ||
    serverMode !== 'api_key' ||
    connection.apiKey ||
    workbench?.apiKey
  )
    return null
  return (
    <section
      className="grid gap-3 rounded-xl border bg-card p-5"
      aria-label={t('rootSetupTitle')}
    >
      <h2 className="text-base font-semibold">{t('rootSetupTitle')}</h2>
      <p className="text-sm text-muted-foreground">
        {t('rootSetupDescription', { account: connection.accountId })}
      </p>
      <div className="flex flex-wrap gap-2">
        <Button onClick={onCreate}>{t('rootSetupCreate')}</Button>
        <Button
          variant="outline"
          nativeButton={false}
          render={
            <Link to="/connections/user-key" search={{ returnTo: '/home' }} />
          }
        >
          {t('rootSetupExisting')}
        </Button>
      </div>
    </section>
  )
}
