import { getStudioExperience } from '#/platform/deployment/experience'
import { StudioEdition } from '#/platform/studio-contract'
import { StudioEntry } from '#/platform/entry/paths'
import { getRouterBasePath } from '#/shared/lib/public-path'

const PREVIEW_KEY = 'ov_studio_stg_preview'

export function readStudioPreview(): string | null {
  try {
    return window.sessionStorage.getItem(PREVIEW_KEY)
  } catch {
    return null
  }
}

// Reload the entire composition: no old query cache, in-flight credential or
// connected provider can survive an edition change. Saved profiles are retained
// and checked by the new edition's connection policy before activation.
export function switchStudioPreview(
  selection: StudioEdition,
  runtime = {
    storage: window.sessionStorage,
    replace: (url: string) => window.location.replace(url),
  },
): void {
  const { preview } = getStudioExperience()
  if (!preview.enabled || !Object.values(StudioEdition).includes(selection))
    return
  runtime.storage.setItem(PREVIEW_KEY, selection)
  runtime.replace(
    `${getRouterBasePath().replace(/\/$/, '')}${StudioEntry.Login}`,
  )
}
