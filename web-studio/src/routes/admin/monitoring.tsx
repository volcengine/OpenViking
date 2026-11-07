import { MonitoringRoute } from '#/features/admin/monitoring/page'
import { TasksRoute } from '#/features/workbench/tasks/page'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '#/shared/ui/tabs'
import { createFileRoute } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'

export const Route = createFileRoute('/admin/monitoring')({
  validateSearch: (search): { tab?: 'tasks' } =>
    search.tab === 'tasks' ? { tab: 'tasks' } : {},
  component: AdminMonitoringPage,
})

function AdminMonitoringPage() {
  const { t } = useTranslation('appShell')
  const { tab } = Route.useSearch()
  const navigate = Route.useNavigate()

  return (
    <Tabs
      className="min-w-0 gap-4"
      value={tab ?? 'components'}
      onValueChange={(value) =>
        void navigate({ search: value === 'tasks' ? { tab: 'tasks' } : {} })
      }
    >
      <TabsList
        aria-label={t('adminNavigation.monitoring')}
        className="gap-0.5 rounded-lg bg-muted p-1 [&_[data-slot=tabs-trigger]]:rounded-md [&_[data-slot=tabs-trigger]]:px-3 [&_[data-slot=tabs-trigger]]:font-normal [&_[data-slot=tabs-trigger]]:outline-none [&_[data-slot=tabs-trigger]]:focus-visible:border-transparent [&_[data-slot=tabs-trigger]]:focus-visible:ring-2 [&_[data-slot=tabs-trigger]]:focus-visible:ring-primary/30 [&_[data-slot=tabs-trigger]]:focus-visible:outline-none [&_[data-slot=tabs-trigger][data-active]]:font-medium"
      >
        <TabsTrigger value="components">
          {t('navigation.monitoring.title')}
        </TabsTrigger>
        <TabsTrigger value="tasks">{t('adminNavigation.tasks')}</TabsTrigger>
      </TabsList>
      <TabsContent value="components">
        <MonitoringRoute />
      </TabsContent>
      <TabsContent value="tasks">
        <TasksRoute management />
      </TabsContent>
    </Tabs>
  )
}
