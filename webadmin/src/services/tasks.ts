import apiClient, { handleAPI, APIResponse } from './api'

// Task types
export interface Task {
  task_id: string
  type: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  progress: number
  created_at: string
  updated_at: string
  completed_at?: string
  error?: string
  result?: unknown
}

export interface TaskStats {
  total: number
  pending: number
  running: number
  completed: number
  failed: number
}

// Task service
export const taskService = {
  /**
   * Get all tasks
   */
  list: async (): Promise<APIResponse<Task[]>> => {
    return handleAPI<Task[]>(
      apiClient.get('/tasks')
    )
  },

  /**
   * Get a specific task
   */
  get: async (task_id: string): Promise<APIResponse<Task>> => {
    return handleAPI<Task>(
      apiClient.get(`/tasks/${task_id}`)
    )
  },

  /**
   * Poll task status until completion
   */
  waitForCompletion: async (
    task_id: string,
    interval: number = 1000,
    timeout: number = 300000 // 5 minutes default
  ): Promise<Task> => {
    const startTime = Date.now()

    return new Promise((resolve, reject) => {
      const poll = async () => {
        const response = await taskService.get(task_id)

        if (!response.success || !response.data) {
          reject(new Error('Task not found'))
          return
        }

        const task = response.data

        // Check timeout
        if (Date.now() - startTime > timeout) {
          reject(new Error('Task timeout'))
          return
        }

        // Check if completed
        if (task.status === 'completed' || task.status === 'failed') {
          resolve(task)
          return
        }

        // Continue polling
        setTimeout(poll, interval)
      }

      poll()
    })
  },

  /**
   * Get task statistics
   */
  getStats: async (): Promise<APIResponse<TaskStats>> => {
    const tasks = await taskService.list()

    if (!tasks.success || !tasks.data) {
      return {
        success: true,
        data: {
          total: 0,
          pending: 0,
          running: 0,
          completed: 0,
          failed: 0
        }
      }
    }

    const stats: TaskStats = {
      total: tasks.data.length,
      pending: 0,
      running: 0,
      completed: 0,
      failed: 0
    }

    tasks.data.forEach(task => {
      switch (task.status) {
        case 'pending':
          stats.pending++
          break
        case 'running':
          stats.running++
          break
        case 'completed':
          stats.completed++
          break
        case 'failed':
          stats.failed++
          break
      }
    })

    return {
      success: true,
      data: stats
    }
  },

  /**
   * Cancel a task
   */
  cancel: async (task_id: string): Promise<APIResponse<void>> => {
    return handleAPI<void>(
      apiClient.delete(`/tasks/${task_id}`)
    )
  },

  /**
   * Get recent tasks
   */
  getRecent: async (limit: number = 10): Promise<APIResponse<Task[]>> => {
    const allTasks = await taskService.list()

    if (!allTasks.success || !allTasks.data) {
      return allTasks
    }

    // Sort by updated_at descending and take limit
    const sorted = allTasks.data.sort(
      (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
    )

    return {
      success: true,
      data: sorted.slice(0, limit)
    }
  }
}

export default taskService
