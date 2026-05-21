export type TempUploadResult = {
  content_type?: string
  file_name?: string
  filename?: string
  size?: number
  temp_file_id: string
}

export type AddResourceResult = {
  errors?: string[]
  message?: string
  root_uri?: string
  status?: 'success' | 'error' | (string & {})
  task_id?: string
  warnings?: string[]
}
