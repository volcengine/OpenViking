import apiClient, { handleAPI, APIResponse } from './api'

// Backend ls entry format
interface BackendFsEntry {
  uri: string
  name: string
  size: number
  isDir: boolean
  modTime: string
  abstract?: string
}

// Filesystem types
export interface FileNode {
  uri: string
  name: string
  type: 'file' | 'directory'
  size?: number
  children?: FileNode[]
  created_at?: string
  updated_at?: string
  abstract?: string
}

export interface FileStat {
  uri: string
  type: string
  size: number
  created_at: string
  updated_at: string
}

export interface DirectoryEntry {
  uri: string
  name: string
  type: 'file' | 'directory'
  size: number
  abstract?: string
}

// Normalize backend entry to DirectoryEntry
const normalizeEntry = (entry: BackendFsEntry): DirectoryEntry => ({
  uri: entry.uri,
  name: entry.name || entry.uri.split('/').pop() || '',
  type: entry.isDir ? 'directory' : 'file',
  size: entry.size || 0,
  abstract: entry.abstract
})

// Normalize backend entry to FileNode
const normalizeFileNode = (entry: BackendFsEntry, children?: FileNode[]): FileNode => ({
  uri: entry.uri,
  name: entry.name || entry.uri.split('/').pop() || '',
  type: entry.isDir ? 'directory' : 'file',
  size: entry.size || 0,
  children,
  created_at: entry.modTime,
  updated_at: entry.modTime,
  abstract: entry.abstract
})

// Filesystem service
export const filesystemService = {
  /**
   * List directory contents
   */
  list: async (
    uri: string,
    recursive: boolean = false,
    limit: number = 100
  ): Promise<APIResponse<DirectoryEntry[]>> => {
    return handleAPI<BackendFsEntry[]>(
      apiClient.get('/fs/ls', { params: { uri, recursive, limit } })
    ).then(res => ({
      ...res,
      data: res.data ? res.data.map(normalizeEntry) : undefined
    }))
  },

  /**
   * Get tree structure
   */
  tree: async (
    uri: string = 'viking://',
    level_limit: number = 3
  ): Promise<APIResponse<FileNode[]>> => {
    return handleAPI<BackendFsEntry[]>(
      apiClient.get('/fs/tree', { params: { uri, level_limit } })
    ).then(res => ({
      ...res,
      data: res.data ? res.data.map(e => normalizeFileNode(e)) : undefined
    }))
  },

  /**
   * Get file/directory stats
   */
  stat: async (uri: string): Promise<APIResponse<FileStat>> => {
    return handleAPI<{ uri: string; size: number; isDir: boolean; modTime: string }>(
      apiClient.get('/fs/stat', { params: { uri } })
    ).then(res => ({
      ...res,
      data: res.data ? {
        uri: res.data.uri,
        type: res.data.isDir ? 'directory' : 'file',
        size: res.data.size || 0,
        created_at: res.data.modTime || '',
        updated_at: res.data.modTime || ''
      } : undefined
    }))
  },

  /**
   * Create directory
   */
  mkdir: async (uri: string): Promise<APIResponse<void>> => {
    return handleAPI<void>(
      apiClient.post('/fs/mkdir', { uri })
    )
  },

  /**
   * Move/rename resource
   */
  mv: async (from_uri: string, to_uri: string): Promise<APIResponse<void>> => {
    return handleAPI<void>(
      apiClient.post('/fs/mv', { from_uri, to_uri })
    )
  },

  /**
   * Delete resource
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
   * Read file content
   */
  read: async (
    uri: string,
    encoding: string = 'utf-8'
  ): Promise<APIResponse<string>> => {
    return handleAPI<string>(
      apiClient.get('/fs/read', { params: { uri, encoding } })
    )
  },

  /**
   * Write file content
   */
  write: async (
    uri: string,
    content: string,
    encoding: string = 'utf-8'
  ): Promise<APIResponse<void>> => {
    return handleAPI<void>(
      apiClient.post('/fs/write', { uri, content, encoding })
    )
  },

  /**
   * Get file content as tree node
   */
  getNode: async (uri: string): Promise<APIResponse<FileNode>> => {
    const stat = await filesystemService.stat(uri)
    if (!stat.success || !stat.data) {
      return { success: false, error: 'File not found' }
    }

    return {
      success: true,
      data: {
        uri: stat.data.uri,
        name: stat.data.uri.split('/').pop() || '',
        type: stat.data.type === 'directory' ? 'directory' : 'file',
        size: stat.data.size,
        created_at: stat.data.created_at,
        updated_at: stat.data.updated_at
      }
    }
  }
}

export default filesystemService
