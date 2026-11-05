import { NewCompile } from '#/features/workbench/compile/new'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/compile/new')({
  validateSearch: (
    s: Record<string, unknown>,
  ): { fromTask?: string; from?: string; compileId?: string } => ({
    compileId:
      typeof s.compileId === 'string' && /^cpt_[0-9a-f]{32}$/.test(s.compileId)
        ? s.compileId
        : undefined,
    fromTask: typeof s.fromTask === 'string' ? s.fromTask : undefined,
    from:
      typeof s.from === 'string' && s.from.startsWith('viking://')
        ? s.from
        : undefined,
  }),
  component: NewCompile,
})
