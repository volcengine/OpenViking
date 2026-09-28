export type UserMemoryPolicy = {
  self?: { enabled: boolean }
  peer?: { enabled: boolean }
  working_memory?: { enabled: boolean }
  memory_types?: string[]
}

export const personalMemoryTypes = [
  'profile',
  'preferences',
  'events',
  'entities',
]
export const agentMemoryTypes = ['cases', 'trajectories', 'experiences']
export const memoryPresets = ['general', 'personal', 'agent'] as const
export type MemoryPreset = (typeof memoryPresets)[number]

export function buildMemoryPolicy(preset: MemoryPreset): UserMemoryPolicy {
  return {
    self: { enabled: true },
    peer: { enabled: preset === 'general' },
    working_memory: { enabled: true },
    ...(preset === 'general'
      ? {}
      : {
          memory_types: [
            ...(preset === 'personal' ? personalMemoryTypes : agentMemoryTypes),
          ],
        }),
  }
}

export function identifyMemoryPreset(
  policy: UserMemoryPolicy,
): MemoryPreset | 'custom' {
  return (
    memoryPresets.find((preset) => {
      const expected = buildMemoryPolicy(preset)
      const types = policy.memory_types
      const expectedTypes = expected.memory_types ?? defaultMemoryTypes
      return (
        policy.self?.enabled !== false &&
        (policy.peer?.enabled !== false) === expected.peer?.enabled &&
        policy.working_memory?.enabled !== false &&
        (types
          ? types.length === expectedTypes.length &&
            expectedTypes.every((type) => types.includes(type))
          : preset === 'general')
      )
    }) ?? 'custom'
  )
}

export const defaultMemoryTypes = [
  ...personalMemoryTypes,
  ...agentMemoryTypes,
  'identity',
  'soul',
]
export const builtinMemoryTypes = [...defaultMemoryTypes, 'skills', 'tools']

export function normalizeMemoryPolicy(
  policy: UserMemoryPolicy,
): UserMemoryPolicy {
  if (!policy.memory_types) return policy
  const types = new Set(policy.memory_types)
  if (types.has('experiences'))
    agentMemoryTypes.forEach((type) => types.add(type))
  else agentMemoryTypes.forEach((type) => types.delete(type))
  return { ...policy, memory_types: [...types] }
}

export const memoryScopeKeys = ['self', 'peer', 'working_memory'] as const
