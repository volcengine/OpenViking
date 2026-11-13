import type {
  GatewayAccess,
  GatewayKey,
  Profile,
  Upstream,
} from '#/features/admin/gateway/lib/api'
import { servedModels } from '#/features/admin/gateway/lib/upstream-schema'

/** Id of the context profile quick mode creates and prefers. */
export const QUICK_PROFILE_ID = 'default'

/** An upstream a new key can relay to without further setup. */
export function isQuickUsable(upstream: Upstream): boolean {
  return (
    upstream.enabled &&
    upstream.auth_mode === 'managed' &&
    upstream.has_api_key &&
    servedModels(upstream).length > 0 &&
    !(upstream.coding_plan && !upstream.allow_coding_plan)
  )
}

/** The profile quick mode uses: `default`, else the first by name. */
export function quickProfile(profiles: Profile[]): Profile | undefined {
  return (
    profiles.find((profile) => profile.id === QUICK_PROFILE_ID) ??
    [...profiles].sort((a, b) => a.name.localeCompare(b.name))[0]
  )
}

export type QuickInputs = {
  keys: GatewayKey[] | undefined
  upstreams: Upstream[] | undefined
  profiles: Profile[] | undefined
  access: GatewayAccess | undefined
  readOnly: boolean
  /** Studio is served by a standalone gateway, which can't import VLM settings. */
  gatewayHost: boolean
}

export type QuickPlan = {
  shown: boolean
  /** Can run now; otherwise only unlocking the management key is offered. */
  runnable: boolean
  /** Usable upstreams the key will get; empty means import first. */
  upstreams: Upstream[]
  /** Existing profile to use; undefined means create the default one. */
  profile?: Profile
  /** Root outside dev mode can't hold a key, so it picks the key's user. */
  pickUser: boolean
}

/** Whether and how the overview offers one-click setup before the first key. */
export function quickPlan({
  keys,
  upstreams,
  profiles,
  access,
  readOnly,
  gatewayHost,
}: QuickInputs): QuickPlan {
  const usable = (upstreams ?? []).filter(isQuickUsable)
  const profile = quickProfile(profiles ?? [])
  const hasUpstream = usable.length > 0
  const runnable =
    (hasUpstream || Boolean(access?.can_import_vlm)) &&
    (Boolean(profile) || !readOnly)
  const locked = Boolean(access?.managed) && readOnly
  const unlockable = locked && !runnable && (hasUpstream || !gatewayHost)
  const loaded = Boolean(keys && upstreams && profiles && access)
  return {
    shown: loaded && !keys?.length && (runnable || unlockable),
    runnable,
    upstreams: usable,
    profile,
    pickUser: access?.role === 'root' && Boolean(access.can_issue_for_others),
  }
}
