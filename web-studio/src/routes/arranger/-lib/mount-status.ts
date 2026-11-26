import type { LiveContextMount, LiveMountStatus } from './model'
import { isWithinRoot, normalizeVikingUri, parentVikingUri } from './uri'

export const mountStatusStyles: Record<
  LiveMountStatus,
  { icon: string; badge: string }
> = {
  VALID: {
    icon: 'text-emerald-600 dark:text-emerald-400',
    badge: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400',
  },
  REVIEW: {
    icon: 'text-amber-600 dark:text-amber-400',
    badge: 'bg-amber-500/10 text-amber-700 dark:text-amber-400',
  },
  INVALID: {
    icon: 'text-destructive',
    badge: 'bg-destructive/10 text-destructive',
  },
  REMOVED: {
    icon: 'text-destructive',
    badge: 'bg-destructive/10 text-destructive',
  },
  UNKNOWN: {
    icon: 'text-muted-foreground',
    badge: 'bg-muted text-muted-foreground',
  },
}

export function getLiveMountStatus(mount: LiveContextMount): LiveMountStatus {
  const status = mount.status
  return status && Object.hasOwn(mountStatusStyles, status) ? status : 'UNKNOWN'
}

export function getAbnormalMountDirectories(
  mounts: ReadonlyMap<string, LiveContextMount>,
  rootUri: string,
): Set<string> {
  const root = normalizeVikingUri(rootUri)
  const directories = new Set<string>()
  for (const [uri, mount] of mounts) {
    if (getLiveMountStatus(mount) === 'VALID' || !isWithinRoot(uri, root)) {
      continue
    }
    let parent = parentVikingUri(uri)
    while (isWithinRoot(parent, root)) {
      if (directories.has(parent)) break
      directories.add(parent)
      if (parent === root) break
      parent = parentVikingUri(parent)
    }
  }
  return directories
}
