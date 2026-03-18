import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { taskService } from '../services/tasks'

// Query keys
export const TASKS_QUERY_KEY = ['tasks']
export const TASK_QUERY_KEY = (id: string) => [...TASKS_QUERY_KEY, id]

// List all tasks hook
export const useTasks = () => {
  return useQuery({
    queryKey: TASKS_QUERY_KEY,
    queryFn: taskService.list,
    refetchInterval: 5000 // Poll every 5 seconds for active tasks
  })
}

// Get specific task hook
export const useTask = (id: string) => {
  return useQuery({
    queryKey: TASK_QUERY_KEY(id),
    queryFn: () => taskService.get(id),
    enabled: !!id,
    refetchInterval: 5000 // Poll every 5 seconds for running tasks
  })
}

// Task stats hook
export const useTaskStats = () => {
  return useQuery({
    queryKey: [...TASKS_QUERY_KEY, 'stats'],
    queryFn: taskService.getStats
  })
}

// Recent tasks hook
export const useRecentTasks = (limit: number = 10) => {
  return useQuery({
    queryKey: [...TASKS_QUERY_KEY, 'recent', limit],
    queryFn: () => taskService.getRecent(limit)
  })
}

// Wait for task completion hook
export const useWaitForTask = () => {
  return useMutation({
    mutationFn: ({ taskId, interval, timeout }: { taskId: string; interval?: number; timeout?: number }) =>
      taskService.waitForCompletion(taskId, interval || 1000, timeout || 300000)
  })
}

// Cancel task mutation
export const useCancelTask = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: taskService.cancel,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: TASKS_QUERY_KEY })
    }
  })
}

export default useTasks
