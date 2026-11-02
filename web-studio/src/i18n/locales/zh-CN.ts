import activity from './zh-CN/activity'
import compile from './zh-CN/compile'
import dataManagement from './zh-CN/data-management'
import gateway from './zh-CN/gateway'
import memoryTemplates from './zh-CN/memory-templates'
import onboarding from './zh-CN/onboarding'
import resources from './zh-CN/resources'
import studio from './zh-CN/studio'
import memoryPolicy from './zh-CN/user-memory-policy'
import vikingbot from './zh-CN/vikingbot'
import workspace from './zh-CN/workspace'

const zhCN = {
  studio,
  onboarding,
  gateway,
  dataManagement,
  compile,
  vikingbot,
  ...workspace,
  ...resources,
  ...activity,
  settings: { ...workspace.settings, memoryPolicy, memoryTemplates },
} as const

export default zhCN
