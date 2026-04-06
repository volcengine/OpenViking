export const routeItems = [
  { key: 'filesystem', label: 'FileSystem', to: '/data/filesystem' as const },
  { key: 'find', label: 'Find', to: '/data/find' as const },
  { key: 'memory', label: 'Add Memory', to: '/data/memory' as const },
  { key: 'ops', label: 'Ops', to: '/legacy/ops' as const },
  { key: 'settings', label: 'Settings', to: '/access/settings' as const },
] as const

export type RouteKey = (typeof routeItems)[number]['key']
