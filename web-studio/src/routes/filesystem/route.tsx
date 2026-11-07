import { validateFilesystemSearch } from '#/features/workbench/filesystem/lib/types'
import { FilesystemRoute } from '#/features/workbench/filesystem/page'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/filesystem')({
  validateSearch: validateFilesystemSearch,
  component: FilesystemRoute,
})
