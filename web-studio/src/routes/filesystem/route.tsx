import type { FilesystemSearch } from '#/features/workbench/filesystem/lib/types'
import { FilesystemRoute } from '#/features/workbench/filesystem/page'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/filesystem')({
  validateSearch: (search: Record<string, unknown>): FilesystemSearch => ({
    file: typeof search.file === 'string' ? search.file : undefined,
    panel:
      search.panel === 'agent' || search.panel === 'terminal'
        ? search.panel
        : undefined,
    session: typeof search.session === 'string' ? search.session : undefined,
    upload: search.upload === true || search.upload === 'true',
    uri: typeof search.uri === 'string' ? search.uri : undefined,
  }),
  component: FilesystemRoute,
})
