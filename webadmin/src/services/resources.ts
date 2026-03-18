import apiClient, { handleAPI, APIResponse } from './api'

// Backend ls entry format
interface BackendLsEntry {
  uri: string
  name: string
  size: number
  isDir: boolean
  modTime: string
  abstract?: string
}

// Resource types
export interface ResourceInfo {
  uri: string
  name: string
  type: 'file' | 'directory'
  size: number
  created_at: string
  updated_at: string
  abstract?: string
}

// Normalize backend ls entry to frontend ResourceInfo
const normalizeResourceInfo = (entry: BackendLsEntry): ResourceInfo => ({
  uri: entry.uri,
  name: entry.name || entry.uri.split('/').pop() || '',
  type: entry.isDir ? 'directory' : 'file',
  size: entry.size || 0,
  created_at: entry.modTime || '',
  updated_at: entry.modTime || '',
  abstract: entry.abstract
})

export interface ResourceStats {
  total_resources: number
  total_size: number
  by_type?: Record<string, number>
  by_directory?: Record<string, number>
  recent_additions?: number
}

export interface AddResourceRequest {
  path: string
  parent?: string
  reason?: string
  wait?: boolean
}

// Backend content response format (string for abstract/overview)
export type BackendContent = string

// Frontend normalized content format
export interface ContentLevel {
  uri: string
  content: string
  tokens?: number
}

// Normalize backend content response
const normalizeContent = (backend: BackendContent, uri?: string): ContentLevel => ({
  uri: uri || '',
  content: typeof backend === 'string' ? backend : ''
})

// Resource service
export const resourceService = {
  /**
   * List resources
   */
  list: async (
    uri: string = 'viking:///',
    limit: number = 50,
    recursive: boolean = false
  ): Promise<APIResponse<ResourceInfo[]>> => {
    return handleAPI<BackendLsEntry[]>(
      apiClient.get('/fs/ls', { params: { uri, simple: false, recursive, limit } })
    ).then(res => ({
      ...res,
      data: res.data ? res.data.map(normalizeResourceInfo) : undefined
    }))
  },

  /**
   * Add a resource
   */
  add: async (
    path: string,
    parent: string = 'viking:///',
    reason: string = '',
    wait: boolean = true
  ): Promise<APIResponse<string>> => {
    return handleAPI<string>(
      apiClient.post('/resources', { path, parent, reason, wait })
    )
  },

  /**
   * Delete a resource
   */
  delete: async (
    uri: string,
    recursive: boolean = false
  ): Promise<APIResponse<void>> => {
    return handleAPI<void>(
      apiClient.delete('/fs', { params: { uri, recursive } })
    )
  },

  /**
   * Read full content (L2 level)
   */
  read: async (
    uri: string,
    offset: number = 0,
    limit: number = -1
  ): Promise<APIResponse<ContentLevel>> => {
    return handleAPI<BackendContent>(
      apiClient.get('/content/read', { params: { uri, offset, limit } })
    ).then(res => ({
      ...res,
      data: res.data ? normalizeContent(res.data, uri) : undefined
    }))
  },

  /**
   * Read abstract (L0 level)
   */
  getAbstract: async (
    uri: string
  ): Promise<APIResponse<ContentLevel>> => {
    return handleAPI<BackendContent>(
      apiClient.get('/content/abstract', { params: { uri } })
    ).then(res => ({
      ...res,
      data: res.data ? normalizeContent(res.data, uri) : undefined
    }))
  },

  /**
   * Read overview (L1 level)
   */
  getOverview: async (
    uri: string
  ): Promise<APIResponse<ContentLevel>> => {
    return handleAPI<BackendContent>(
      apiClient.get('/content/overview', { params: { uri } })
    ).then(res => ({
      ...res,
      data: res.data ? normalizeContent(res.data, uri) : undefined
    }))
  },

  /**
   * Get resource stats
   */
  getStats: async (): Promise<APIResponse<ResourceStats>> => {
    // This would need a dedicated API endpoint
    return {
      success: true,
      data: {
        total_resources: 0,
        total_size: 0
      }
    }
  },

  /**
   * Batch add resources
   */
  batchAdd: async (
    paths: string[],
    parent: string = 'viking:///',
    reason: string = ''
  ): Promise<APIResponse<string[]>> => {
    return handleAPI<string[]>(
      apiClient.post('', {
        method: 'POST',
        path: '/api/v1/resources/batch',
        data: { paths, parent, reason, wait: true }
      })
    )
  },

  /**
   * Batch delete resources
   */
  batchDelete: async (
    uris: string[],
    recursive: boolean = false
  ): Promise<APIResponse<void>> => {
    return handleAPI<void>(
      apiClient.post('', {
        method: 'DELETE',
        path: '/api/v1/resources/batch',
        data: { uris, recursive }
      })
    )
  }
}

export default resourceService
