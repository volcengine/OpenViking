export type TempUploadResult = {
  content_type?: string
  file_name?: string
  filename?: string
  size?: number
  temp_file_id: string
}

export type ResourceProcessingMode = 'semantic_and_vectors' | 'vectors_only'

export type ResourceTagMode = 'replace' | 'append'

export type ResourceImportArgs = Record<string, unknown> & {
  allow_external_links?: boolean
  auth_config?: Record<string, unknown>
  branch?: string
  commit?: string
  depth?: number
  exclude_paths?: string[]
  feishu_access_token?: string
  feishu_refresh_token?: string
  include_paths?: string[]
  max_pages?: number
  parse_mode?: 'default' | 'no_split'
  site?: boolean
  skip_download_links?: boolean
}

/**
 * Web Studio resource-import payload. Keep compatibility with the currently
 * deployed API here so feature code does not depend on regenerating a client.
 */
export type ResourceImportRequest = {
  path?: string
  temp_file_id?: string
  add_type?: string
  to?: string
  parent?: string
  create_parent?: boolean
  reason?: string
  instruction?: string
  wait?: boolean
  timeout?: number
  strict?: boolean
  source_name?: string
  ignore_dirs?: string
  include?: string
  exclude?: string
  directly_upload_media?: boolean
  preserve_structure?: boolean
  args?: ResourceImportArgs
  telemetry?: boolean | Record<string, boolean>
  watch_interval?: number
  processing_mode?: ResourceProcessingMode
  tags?: string[]
  tag_mode?: ResourceTagMode
}

export type ResourceImportCommonBody = Omit<
  ResourceImportRequest,
  'path' | 'temp_file_id'
>

export type ResourceImportResult = {
  errors?: string[]
  message?: string
  root_uri?: string
  status?: 'success' | 'error' | (string & {})
  task_id?: string
  warnings?: string[]
}
