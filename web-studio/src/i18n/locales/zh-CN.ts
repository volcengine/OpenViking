import workspace from './zh-CN/workspace'
import resources from './zh-CN/resources'
import activity from './zh-CN/activity'

const zhCN = {
  ...workspace,
  ...resources,
  ...activity,
} as const

export default zhCN
