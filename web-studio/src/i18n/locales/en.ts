import activity from './en/activity'
import compile from './en/compile'
import dataManagement from './en/data-management'
import gateway from './en/gateway'
import memoryTemplates from './en/memory-templates'
import onboarding from './en/onboarding'
import resources from './en/resources'
import studio from './en/studio'
import memoryPolicy from './en/user-memory-policy'
import vikingbot from './en/vikingbot'
import workspace from './en/workspace'

const en = {
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

export default en
