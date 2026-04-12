export type VikingFileType = 'image' | 'markdown' | 'code' | 'text' | 'binary'

export interface VikingFsEntry {
  uri: string
  name: string
  isDir: boolean
  size: string
  sizeBytes: number | null
  modTime: string
  modTimestamp: number | null
  abstract: string
  tags?: string
}

export interface VikingListQueryOptions {
  output?: 'agent' | 'original'
  showAllHidden?: boolean
  nodeLimit?: number
  limit?: number | null
  absLimit?: number
  recursive?: boolean
  simple?: boolean
}

export interface VikingTreeQueryOptions {
  output?: 'agent' | 'original'
  showAllHidden?: boolean
  nodeLimit?: number
  limit?: number | null
  absLimit?: number
  levelLimit?: number
}

export interface VikingReadQueryOptions {
  offset?: number
  limit?: number
}

export interface VikingListResult {
  uri: string
  entries: Array<VikingFsEntry>
}

export interface VikingTreeResult {
  rootUri: string
  nodes: Array<VikingFsEntry>
}

export interface VikingReadResult {
  uri: string
  content: string
  offset: number
  limit: number
  truncated: boolean
}

export interface VikingPreviewPolicy {
  maxAutoReadBytes?: number
  defaultReadLimit?: number
}

export interface VikingPreviewResult {
  entry: VikingFsEntry
  fileType: VikingFileType
  shouldAutoRead: boolean
  reason?: 'binary' | 'too-large'
  content: string
  offset: number
  limit: number
  truncated: boolean
}

export interface VikingApiError {
  code: string
  message: string
  statusCode?: number
  details?: unknown
}
