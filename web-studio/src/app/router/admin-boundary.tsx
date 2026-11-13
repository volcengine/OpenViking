import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { useStudioMode } from '#/features/connection/hooks/use-studio-mode'
import { isConnectionSettingsPath } from '#/features/connection/lib/studio-mode'
import { Button } from '#/shared/ui/button'
import { Link, Outlet, useRouterState } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'

export function AdminRoute() {
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
