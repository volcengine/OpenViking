import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { filesystemService } from '../services/filesystem'

// Query keys
export const FILESYSTEM_QUERY_KEY = ['filesystem']
export const FS_LIST_QUERY_KEY = (uri: string) => [...FILESYSTEM_QUERY_KEY, 'list', uri]
export const FS_TREE_QUERY_KEY = (uri: string) => [...FILESYSTEM_QUERY_KEY, 'tree', uri]
export const FS_STAT_QUERY_KEY = (uri: string) => [...FILESYSTEM_QUERY_KEY, 'stat', uri]

// List directory hook
export const useFileSystemList = (
  uri: string,
  recursive: boolean = false,
  limit: number = 100
) => {
  return useQuery({
    queryKey: FS_LIST_QUERY_KEY(uri),
    queryFn: () => filesystemService.list(uri, recursive, limit),
    enabled: !!uri
  })
}

// Tree structure hook
export const useFileSystemTree = (
  uri: string = 'viking://',
  level_limit: number = 3
) => {
  return useQuery({
    queryKey: FS_TREE_QUERY_KEY(uri),
    queryFn: () => filesystemService.tree(uri, level_limit),
    enabled: !!uri
  })
}

// Stat hook
export const useFileSystemStat = (uri: string) => {
  return useQuery({
    queryKey: FS_STAT_QUERY_KEY(uri),
    queryFn: () => filesystemService.stat(uri),
    enabled: !!uri
  })
}

// Create directory mutation
export const useMkDir = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (uri: string) => filesystemService.mkdir(uri),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: FILESYSTEM_QUERY_KEY })
    }
  })
}

// Delete file resource mutation
export const useDeleteFileResource = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ uri, recursive }: { uri: string; recursive: boolean }) =>
      filesystemService.delete(uri, recursive),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: FILESYSTEM_QUERY_KEY })
    }
  })
}

// Move resource mutation
export const useMoveResource = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ from_uri, to_uri }: { from_uri: string; to_uri: string }) =>
      filesystemService.mv(from_uri, to_uri),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: FILESYSTEM_QUERY_KEY })
    }
  })
}

// Read file content hook
export const useReadFile = (uri: string, encoding: string = 'utf-8') => {
  return useQuery({
    queryKey: [...FILESYSTEM_QUERY_KEY, 'read', uri],
    queryFn: () => filesystemService.read(uri, encoding),
    enabled: !!uri
  })
}

export default useFileSystemList
