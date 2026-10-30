export type ServiceProvider = 'opensource' | 'volcengine' | 'unknown'
export type ServiceSelection = 'auto' | Exclude<ServiceProvider, 'unknown'>
export type StudioDomain = 'workspace' | 'extensions' | 'management'
export type CapabilityState =
  | 'supported'
  | 'unavailable'
  | 'forbidden'
  | 'unknown'

// URL detection identifies the deployment, not the permissions of its key.
// Version numbers and encoded key contents are not provider identifiers.
export function identifyServiceProvider(baseUrl: string): ServiceProvider {
  try {
    const { hostname, protocol } = new URL(baseUrl)
    if (!['http:', 'https:'].includes(protocol)) return 'unknown'
    if (/^api\.vikingdb\.[a-z0-9-]+\.volces\.com$/.test(hostname)) {
      return 'volcengine'
    }
    // All other HTTP deployments default to open source; hosted proxies can override this.
    return 'opensource'
  } catch {
    // An unfinished URL is not a deployment classification.
  }
  return 'unknown'
}

// Configuration can be edited before authentication succeeds. It does not grant access.
export function resolveConfiguredServiceProvider(
  baseUrl: string,
  selection: ServiceSelection = 'auto',
): ServiceProvider {
  const detected = identifyServiceProvider(baseUrl)
  return detected === 'volcengine'
    ? detected
    : selection === 'auto'
      ? detected
      : selection
}

export const STUDIO_FEATURES = [
  { id: 'filesystem', domain: 'workspace', to: '/filesystem' },
  { id: 'directory', domain: 'workspace', to: '/directory' },
  { id: 'memories', domain: 'workspace', to: '/memories' },
  { id: 'retrieval', domain: 'workspace', to: '/retrieval' },
  { id: 'skills', domain: 'workspace', to: '/skills' },
  { id: 'sessions', domain: 'workspace', to: '/sessions' },
  { id: 'tasks', domain: 'workspace', to: '/tasks' },
  // These pages need additional endpoints or mutations that have not been
  // adapted for hosted deployments. One healthy endpoint cannot enable them.
  { id: 'home', domain: 'extensions', to: '/home' },

  { id: 'vikingbot', domain: 'extensions', to: '/vikingbot' },
  { id: 'compile', domain: 'extensions', to: '/compile' },
  { id: 'agentExperience', domain: 'extensions', to: '/agent-experience' },
  {
    id: 'requestLogs',
    domain: 'extensions',
    to: '/request-logs',
    probe: '/api/v1/console/audit',
  },
  {
    id: 'watches',
    domain: 'extensions',
    to: '/watches',
    probe: '/api/v1/watches',
  },
  {
    id: 'monitoring',
    domain: 'extensions',
    to: '/monitoring',
    probe: '/api/v1/observer/system',
  },
  { id: 'gateway', domain: 'management', to: '/admin/gateway' },
  { id: 'botManagement', domain: 'management', to: '/admin/vikingbot' },
  { id: 'configuration', domain: 'management', to: '/admin/configuration' },
  {
    id: 'agentEvolutionSettings',
    domain: 'management',
    to: '/admin/agent-evolution',
  },
  { id: 'users', domain: 'management', to: '/admin/users' },
  { id: 'permissions', domain: 'management', to: '/permissions' },
] as const satisfies ReadonlyArray<{
  id: string
  domain: StudioDomain
  to: string
  probe?: string
}>

export function featureForPath(pathname: string) {
  return STUDIO_FEATURES.find(
    (f) => pathname === f.to || pathname.startsWith(`${f.to}/`),
  )
}

export function classifyCapabilityError(error: {
  code?: string
  statusCode?: number
  responseBody?: unknown
}): CapabilityState {
  const body = error.responseBody as
    | { ResponseMetadata?: { Error?: { Code?: string } } }
    | undefined
  if (
    error.code === 'ApiBlocked' ||
    body?.ResponseMetadata?.Error?.Code === 'ApiBlocked'
  )
    return 'unavailable'
  if (error.statusCode === 403) return 'forbidden'
  // Only used for object-free endpoint probes, never file/stat requests.
  if (error.statusCode === 404 || error.statusCode === 405) return 'unavailable'
  return 'unknown'
}

export function isStudioFeatureVisible(
  provider: ServiceProvider,
  feature: (typeof STUDIO_FEATURES)[number],
  capability?: CapabilityState,
): boolean {
  if (feature.domain === 'management') return provider === 'opensource'
  if (feature.domain !== 'extensions') return true
  if (provider === 'opensource') return true
  return (
    provider === 'volcengine' &&
    'probe' in feature &&
    capability === 'supported'
  )
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

// A reachable endpoint is not enough: verify the read contract used by its page.
export function classifyCapabilityResponse(
  featureId: string,
  payload: unknown,
): CapabilityState {
  if (!isRecord(payload)) return 'unknown'
  const metadata = payload.ResponseMetadata
  if (
    payload.status === 'error' ||
    payload.error ||
    (isRecord(metadata) && metadata.Error)
  ) {
    const error = isRecord(payload.error) ? payload.error : undefined
    return classifyCapabilityError({
      code: typeof error?.code === 'string' ? error.code : undefined,
      responseBody: payload,
    })
  }
  const result = 'result' in payload ? payload.result : payload
  if (!isRecord(result)) return 'unknown'
  if (result.enabled === false) return 'unavailable'
  switch (featureId) {
    case 'watches':
      return Array.isArray(result.tasks) ? 'supported' : 'unknown'
    case 'requestLogs':
      return Array.isArray(result.items) && typeof result.total === 'number'
        ? 'supported'
        : 'unknown'
    case 'monitoring':
      return typeof result.is_healthy === 'boolean' &&
        isRecord(result.components)
        ? 'supported'
        : 'unknown'
    default:
      return 'unknown'
  }
}
