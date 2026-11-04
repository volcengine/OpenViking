import { ServerConfigurationEditor } from '#/features/admin/server-configuration/components/server-configuration'
import { useConnectionAccess } from '#/features/connection/access'
import { useTranslation } from 'react-i18next'

export function ServerConfigurationRoute() {
  const { t } = useTranslation('appShell')
  const { connectionRole, identityScopeKey } = useConnectionAccess()
  if (connectionRole !== 'root') return <p>{t('modes.rootRequired')}</p>
  return <ServerConfigurationEditor key={identityScopeKey} />
}
