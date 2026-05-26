export type FsOutputFormat = 'agent' | 'original' | (string & {})

export type FileStat = {
  abstract?: string
  contentLength?: number
  content_length?: number
  description?: string
  id?: string
  isDir?: boolean
  is_dir?: boolean
  modTime?: string
  mod_time?: string
  modifiedAt?: string
  modified_at?: string
  name?: string
  path?: string
  relative_path?: string
  size?: number | string
  size_bytes?: number
  summary?: string
  type?: string
  updatedAt?: string
  updated_at?: string
  uri?: string
}

export type FSListResult = string[] | FileStat[]

export type FSTreeResult = FSListResult

export type FSStatResult = FileStat
