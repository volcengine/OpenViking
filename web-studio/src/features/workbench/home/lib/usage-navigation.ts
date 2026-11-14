import { validateFilesystemSearch } from '#/features/workbench/filesystem/lib/types'

export function validateUsageSearch(search: Record<string, unknown>) {
  const { file, uri, panel, session } = validateFilesystemSearch(search)
  return { file, uri, panel, session }
}
