import workspace from './en/workspace'
import resources from './en/resources'
import activity from './en/activity'

const en = {
  ...workspace,
  ...resources,
  ...activity,
} as const

export default en
