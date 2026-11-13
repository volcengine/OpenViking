import { ServerConfigurationEditor } from '#/features/admin/server-configuration/components/server-configuration'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { useTranslation } from 'react-i18next'

export function ServerConfigurationRoute() {
  const { t } = useTranslation('appShell')
  const { connectionRole, identityScopeKey } = useAppConnection()
  if (connectionRole !== 'root') return <p>{t('modes.rootRequired')}</p>
  return <ServerConfigurationEditor key={identityScopeKey} />
}
