import onboarding from './zh-CN/onboarding'
import studio from './zh-CN/studio'
import vikingbot from './zh-CN/vikingbot'
import gateway from './zh-CN/gateway'
import compile from './zh-CN/compile'
import memoryPolicy from './zh-CN/user-memory-policy'
import workspace from './zh-CN/workspace'
import resources from './zh-CN/resources'
import activity from './zh-CN/activity'
import memoryTemplates from './zh-CN/memory-templates'
import arranger from './zh-CN/arranger'
import dataManagement from './zh-CN/data-management'

const zhCN = {
  arranger,
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
