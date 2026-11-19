import { validateFilesystemSearch } from '#/features/workbench/filesystem/lib/types'
import { TasksRoute } from '#/features/workbench/tasks/page'
import { createFileRoute, Link, useSearch } from '@tanstack/react-router'
import { ArrowLeftIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

export const Route = createFileRoute('/tasks')({
  validateSearch: (search) => {
    const { file, uri, panel, session } = validateFilesystemSearch(search)
    return { file, uri, panel, session }
  },
  component: WorkbenchTasksPage,
})

function WorkbenchTasksPage() {
  const search = useSearch({ from: '/tasks' })
  const { t } = useTranslation('tasksPage')

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <Link
        to="/filesystem"
        search={search}
        className="inline-flex w-fit items-center gap-1.5 rounded-sm text-sm text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
      >
        <ArrowLeftIcon className="size-4" />
        {t('backToWorkbench')}
      </Link>
      <TasksRoute />
    </div>
  )
}
