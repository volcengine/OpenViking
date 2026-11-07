import { HomePage } from './page'
import { Link, useSearch } from '@tanstack/react-router'
import { ArrowLeftIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

export function UsagePage() {
  const search = useSearch({ from: '/usage' })
  const { t } = useTranslation('tasksPage')
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <Link
        to="/filesystem"
        search={search}
        className="inline-flex w-fit items-center gap-1.5 rounded-sm text-sm text-muted-foreground hover:text-foreground focus-visible:outline-1 focus-visible:outline-primary"
      >
        <ArrowLeftIcon className="size-4" />
        {t('backToWorkbench')}
      </Link>
      <HomePage />
    </div>
  )
}
