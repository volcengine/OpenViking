import type { JsonObject } from '../../common'

export type ContentReadResult = string | JsonObject

export type ContentDownloadQuery = {
  uri: string
}

export type ContentWriteResult = JsonObject & {
  message?: string
  status?: string
  task_id?: string
  updated?: boolean
  uri?: string
}
