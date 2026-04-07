export const legacyRouteItems = [
  { key: 'access', label: 'Access', to: '/legacy/access' as const },
  { key: 'data', label: 'Data', to: '/legacy/data' as const },
  { key: 'ops', label: 'Ops', to: '/legacy/ops' as const },
] as const

export type LegacyRouteKey = (typeof legacyRouteItems)[number]['key']
