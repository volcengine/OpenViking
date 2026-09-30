import vikingbot from './en/vikingbot'
import compile from './en/compile'
import memoryPolicy from './en/user-memory-policy'
import workspace from './en/workspace'
import resources from './en/resources'
import activity from './en/activity'
import memoryTemplates from './en/memory-templates'

const en = {
  compile,
  vikingbot,
  ...workspace,
  ...resources,
  ...activity,
  settings: { ...workspace.settings, memoryPolicy, memoryTemplates },
} as const

export default en
