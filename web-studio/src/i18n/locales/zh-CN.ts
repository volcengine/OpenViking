import vikingbot from './zh-CN/vikingbot'
import contextGateway from './zh-CN/context-gateway'
import compile from './zh-CN/compile'
import memoryPolicy from './zh-CN/user-memory-policy'
import workspace from './zh-CN/workspace'
import resources from './zh-CN/resources'
import activity from './zh-CN/activity'
import memoryTemplates from './zh-CN/memory-templates'
import ttl from './zh-CN/ttl'

const zhCN = {
  contextGateway,
  compile,
  vikingbot,
  ...workspace,
  ...resources,
  ...activity,
  settings: { ...workspace.settings, memoryPolicy, memoryTemplates, ttl },
} as const

export default zhCN
