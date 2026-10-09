import vikingbot from './en/vikingbot'
import compile from './en/compile'
import memoryPolicy from './en/user-memory-policy'
import workspace from './en/workspace'
import resources from './en/resources'
import activity from './en/activity'
import memoryTemplates from './en/memory-templates'
import ttl from './en/ttl'
import contextGateway from './en/context-gateway'

const en = {
  contextGateway,
  compile,
  vikingbot,
  ...workspace,
  ...resources,
  ...activity,
  settings: { ...workspace.settings, memoryPolicy, memoryTemplates, ttl },
} as const

export default en
