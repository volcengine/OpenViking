import { SessionsPage } from '#/features/workbench/sessions/index'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/sessions/')({
  component: SessionsPage,
  validateSearch: (search: Record<string, unknown>) =>
    ({
      s: (search.s as string) || undefined,
    }) as { s?: string },
})
