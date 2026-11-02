import { supportsNativeManagement } from '../deployment/experience'
import type { ServiceProvider } from '../studio-contract'

export type StudioDomain = 'workspace' | 'extensions' | 'management'
export type CapabilityState =
  | 'supported'
  | 'unavailable'
  | 'forbidden'
  | 'unknown'

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

export function isStudioFeatureVisible(
  provider: ServiceProvider,
  feature: (typeof STUDIO_FEATURES)[number],
  capability?: CapabilityState,
): boolean {
  if (feature.domain === 'management') return supportsNativeManagement(provider)
  if (feature.domain !== 'extensions') return true
  if (provider === 'opensource') return true
  return (
    provider === 'volcengine' &&
    'probe' in feature &&
    capability === 'supported'
  )
}
