import { getStudioExperience } from '#/platform/deployment/experience'
import { FilesystemRoute } from '#/features/workbench/filesystem/page'

export function FilesystemEntry() {
  return (
    <FilesystemRoute
      appearance={getStudioExperience().presentation.filesystem}
    />
  )
}
