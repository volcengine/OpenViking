import { CompileList } from '#/features/workbench/compile/index'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/compile/')({
  validateSearch: (
    search: Record<string, unknown>,
  ): { status?: string; q?: string } => ({
    status: typeof search.status === 'string' ? search.status : '',
    q: typeof search.q === 'string' ? search.q : '',
  }),
  component: CompileList,
})
