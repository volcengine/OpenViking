import { UsagePage } from '#/features/workbench/home/usage-page'
import { validateUsageSearch } from '#/features/workbench/home/lib/usage-navigation'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/usage')({
  validateSearch: validateUsageSearch,
  component: UsagePage,
})
