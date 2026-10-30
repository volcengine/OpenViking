import {
  createFileRoute,
  Outlet,
  Link,
  useRouterState,
} from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { isConnectionSettingsPath } from '#/lib/studio-mode'
import { useStudioMode } from '#/hooks/use-studio-mode'
import { useAppConnection } from '#/hooks/use-app-connection'
import { Button } from '#/components/ui/button'

export const Route = createFileRoute('/admin')({ component: AdminRoute })
function AdminRoute() {
  const { t } = useTranslation('appShell')
  const { canManage } = useStudioMode()
  const { isConnectionRoleLoading, isGatewayHost } = useAppConnection()
  const pathname = useRouterState({ select: (s) => s.location.pathname })
  // A standalone gateway keeps its independent /access authorization.
  const standaloneGateway =
    isGatewayHost &&
    (pathname === '/admin/gateway' || pathname.startsWith('/admin/gateway/'))
  if (isConnectionSettingsPath(pathname) || standaloneGateway) return <Outlet />
  if (isConnectionRoleLoading) return <p role="status">{t('modes.checking')}</p>
  if (!canManage)
    return (
      <section className="mx-auto grid w-full max-w-xl gap-4 rounded-xl border bg-card p-6">
        <h1 className="text-lg font-medium">{t('modes.unavailable')}</h1>
        <p className="text-sm text-muted-foreground">
          {t('modes.unavailableDescription')}
        </p>
        <Button
          nativeButton={false}
          variant="outline"
          render={<Link to="/admin/settings" />}
        >
          {t('footer.connection')}
        </Button>
      </section>
    )
  return <Outlet />
}
