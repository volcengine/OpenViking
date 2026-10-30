import { createFileRoute } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { useAppConnection } from '#/hooks/use-app-connection'
import { ServerConfigurationEditor } from '#/routes/settings/-components/server-configuration'

export const Route = createFileRoute('/admin/configuration/server')({
  component: ServerConfigurationRoute,
})
function ServerConfigurationRoute() {
  const { t } = useTranslation('appShell')
  const { connectionRole, identityScopeKey } = useAppConnection()
  if (connectionRole !== 'root') return <p>{t('modes.rootRequired')}</p>
  return <ServerConfigurationEditor key={identityScopeKey} />
}
