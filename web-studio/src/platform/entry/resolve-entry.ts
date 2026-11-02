import type { ServiceProvider, StudioMode } from '../studio-contract'
import {
  getStudioExperience,
  supportsNativeManagement,
} from '../deployment/experience'
import { StudioEntry } from './paths'

/** Navigation result derived by the connection module; not an API authorization. */
export type StudioEntryAccess = 'workbench' | 'management' | 'both'

export function resolveStudioEntry(
  {
    access,
    provider,
    preferredMode,
  }: {
    access: StudioEntryAccess
    provider: ServiceProvider
    preferredMode: StudioMode | null
  },
  experience = getStudioExperience(),
) {
  const workbench = experience.entry.workbench[provider]
  // Preserve current hosted behavior; hosted ROOT integration is a separate task.
  if (!supportsNativeManagement(provider, experience)) return workbench
  if (
    access === 'management' ||
    (access === 'both' && preferredMode === 'management')
  )
    return StudioEntry.Management
  return workbench
}
