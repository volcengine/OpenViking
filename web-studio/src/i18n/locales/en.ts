import onboarding from './en/onboarding'
import studio from './en/studio'
import vikingbot from './en/vikingbot'
import compile from './en/compile'
import memoryPolicy from './en/user-memory-policy'
import workspace from './en/workspace'
import resources from './en/resources'
import activity from './en/activity'
import memoryTemplates from './en/memory-templates'
import gateway from './en/gateway'
import arranger from './en/arranger'
import dataManagement from './en/data-management'

const en = {
  studio,
  onboarding,
  arranger,
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
