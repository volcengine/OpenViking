import { NewCompile } from '#/features/workbench/compile/new'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/compile/new')({
  validateSearch: (
    s: Record<string, unknown>,
  ): {
    fromTask?: string
    from?: string
    target?: string
    compileName?: string
  } => ({
    compileName:
      typeof s.compileName === 'string' &&
      /^[A-Za-z0-9_][A-Za-z0-9_-]{0,63}$/.test(s.compileName)
        ? s.compileName
        : undefined,
    target:
      typeof s.target === 'string' && s.target.startsWith('viking://')
        ? s.target
        : undefined,
    fromTask: typeof s.fromTask === 'string' ? s.fromTask : undefined,
    from:
      typeof s.from === 'string' && s.from.startsWith('viking://')
        ? s.from
        : undefined,
  }),
  component: NewCompile,
})
