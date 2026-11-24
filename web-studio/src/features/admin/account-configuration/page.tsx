import { EvolutionSettingRow } from '#/features/admin/account-configuration/experience-setting'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { useTranslation } from 'react-i18next'

export function OpenVikingConfigurationPage() {
  const { t } = useTranslation('appShell')
  const { connection, identityScopeKey } = useAppConnection()
  return (
    <section className="mx-auto grid w-full max-w-3xl gap-6">
      <header className="grid gap-2">
        <h1 className="text-2xl font-semibold">{t('modes.configuration')}</h1>
        <p className="text-sm text-muted-foreground">
          {t('modes.configurationDescription')}
        </p>
      </header>
      <div className="overflow-hidden rounded-xl border bg-card">
        <h2 className="px-6 py-4 font-medium">
          {t('modes.accountConfiguration', { account: connection.accountId })}
        </h2>
        <EvolutionSettingRow key={identityScopeKey} />
      </div>
    </section>
  )
}
