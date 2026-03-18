import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { resourceService, AddResourceRequest } from '../services/resources'

// Query keys
export const RESOURCES_QUERY_KEY = ['resources']
export const RESOURCE_QUERY_KEY = (uri: string) => [...RESOURCES_QUERY_KEY, uri]

// Hook options
export interface UseResourcesOptions {
  uri?: string
  limit?: number
  recursive?: boolean
}

// List resources hook
export const useResources = (options: UseResourcesOptions = {}) => {
  const { uri = 'viking:///', limit = 50, recursive = false } = options

  return useQuery({
    queryKey: [...RESOURCES_QUERY_KEY, uri, limit, recursive],
    queryFn: () => resourceService.list(uri, limit, recursive),
    refetchInterval: 60000
  })
}

// Get resource hook
export const useResource = (uri: string) => {
  return useQuery({
    queryKey: RESOURCE_QUERY_KEY(uri),
    queryFn: () => resourceService.getAbstract(uri),
    enabled: !!uri
  })
}

// Add resource mutation
export const useAddResource = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (data: AddResourceRequest) => resourceService.add(data.path, data.parent, data.reason, data.wait),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: RESOURCES_QUERY_KEY })
    }
  })
}

// Delete resource mutation
export const useDeleteResource = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (uri: string) => resourceService.delete(uri),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: RESOURCES_QUERY_KEY })
    }
  })
}

// Read content hook
export const useReadContent = (uri: string, offset: number = 0, limit: number = -1) => {
  return useQuery({
    queryKey: [...RESOURCE_QUERY_KEY(uri), 'content', offset, limit],
    queryFn: () => resourceService.read(uri, offset, limit),
    enabled: !!uri
  })
}

// Get abstract hook
export const useGetAbstract = (uri: string) => {
  return useQuery({
    queryKey: [...RESOURCE_QUERY_KEY(uri), 'abstract'],
    queryFn: () => resourceService.getAbstract(uri),
    enabled: !!uri
  })
}

// Get overview hook
export const useGetOverview = (uri: string) => {
  return useQuery({
    queryKey: [...RESOURCE_QUERY_KEY(uri), 'overview'],
    queryFn: () => resourceService.getOverview(uri),
    enabled: !!uri
  })
}

export default useResources
