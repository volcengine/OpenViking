import vikingbot from './zh-CN/vikingbot'
import compile from './zh-CN/compile'
import memoryPolicy from './zh-CN/user-memory-policy'
import workspace from './zh-CN/workspace'
import resources from './zh-CN/resources'
import activity from './zh-CN/activity'

const zhCN = {
  compile,
  vikingbot,
  ...workspace,
  ...resources,
  ...activity,
  settings: { ...workspace.settings, memoryPolicy },
} as const

export default zhCN
