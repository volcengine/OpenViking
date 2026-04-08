export const routeItems = [
  { key: 'filesystem', to: '/data/filesystem' as const },
  { key: 'find', to: '/data/find' as const },
  { key: 'memory', to: '/data/memory' as const },
  { key: 'ops', to: '/legacy/ops' as const },
  { key: 'settings', to: '/access/settings' as const },
] as const

export type RouteKey = (typeof routeItems)[number]['key']
