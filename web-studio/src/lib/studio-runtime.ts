import { resolvePublicAsset } from '#/lib/public-path'

export type StudioRuntimeConfig = {
  proxyMode: boolean
  baseUrl: string
  hasManagedAccount: boolean
  hasManagedUser: boolean
}

const DEFAULT_CONFIG: StudioRuntimeConfig = {
  proxyMode: false,
  baseUrl: '',
  hasManagedAccount: false,
  hasManagedUser: false,
}

const RUNTIME_CONFIG_PATH = '_studio/runtime-config.json'

let cached: StudioRuntimeConfig | null = null
let pending: Promise<StudioRuntimeConfig> | null = null

export function getStudioRuntime(): StudioRuntimeConfig {
  return cached ?? DEFAULT_CONFIG
}

export async function loadStudioRuntime(): Promise<StudioRuntimeConfig> {
  if (cached) return cached
  if (pending) return pending

  pending = fetchRuntimeConfig().then((config) => {
    cached = config
    pending = null
    return config
  })
  return pending
}

async function fetchRuntimeConfig(): Promise<StudioRuntimeConfig> {
  if (typeof window === 'undefined' || typeof fetch === 'undefined') {
    return DEFAULT_CONFIG
  }

  try {
    const response = await fetch(resolvePublicAsset(RUNTIME_CONFIG_PATH), {
      cache: 'no-store',
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    })
    if (!response.ok) return DEFAULT_CONFIG
    const raw: unknown = await response.json()
    if (!raw || typeof raw !== 'object') return DEFAULT_CONFIG
    const data = raw as Partial<StudioRuntimeConfig>
    return {
      ...DEFAULT_CONFIG,
      proxyMode: Boolean(data.proxyMode),
      hasManagedAccount: Boolean(data.hasManagedAccount),
      hasManagedUser: Boolean(data.hasManagedUser),
      baseUrl: typeof data.baseUrl === 'string' ? data.baseUrl : '',
    }
  } catch {
    return DEFAULT_CONFIG
  }
}
