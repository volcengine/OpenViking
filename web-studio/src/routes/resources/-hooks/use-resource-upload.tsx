import * as React from 'react'
import { toast } from 'sonner'

import {
  getTasks,
  getOvResult,
  isOvClientError,
  postResourcesTempUpload,
} from '#/lib/ov-client'
import { parseUploadError } from '../-lib/upload'
import {
  isUploadStatusActive,
  mergeServerTasks,
  normalizeTaskList,
} from '../-lib/resource-upload-tasks'
import {
  buildRemoteResourceRequest,
  buildUploadedResourceRequest,
} from '../-lib/resource-import-request'
import { postResourceImport } from '../-lib/resource-import-api'
import type {
  ResourceImportCommonBody,
  ResourceImportResult,
  TempUploadResult,
} from '../-lib/resource-import-types'
import type { TaskListResult } from '@ov-server/api/v1/tasks'

export type ResourceUploadTaskStatus =
  | 'cancelled'
  | 'pending'
  | 'uploading'
  | 'processing'
  | 'success'
  | 'failed'

export type ResourceUploadTask = {
  id: string
  source: 'local' | 'remote' | 'server'
  serverTaskId: string | null
  fileName: string
  fileSize: number | null
  fileType: string | null
  status: ResourceUploadTaskStatus
  progress: number | null
  createdAt: number
  finishedAt: number | null
  errorCode: string | null
  errorMessage: string | null
  rootUri: string | null
}

export type RemoteUploadPhase = 'idle' | 'processing' | 'done'

export type RemoteUploadState = {
  phase: RemoteUploadPhase
  skippedFiles: string[]
  error: string | null
  remoteUrl: string
  taskId: string | null
}

export type UploadBatchItem = {
  file: File
  fileType: string | null
}

export type UploadBatchParams = {
  files: UploadBatchItem[]
  commonBody: ResourceImportCommonBody
}

export type RemoteStartResult = {
  rootUri: string | null
  taskId: string | null
}

export type RemoteStartParams = {
  url: string
  commonBody: ResourceImportCommonBody
  onAccepted?: (result: RemoteStartResult) => void
  onCompleted?: () => void
  onFailed?: () => void
}

type ResourceUploadContextValue = {
  tasks: ResourceUploadTask[]
  remoteState: RemoteUploadState
  enqueueUploads: (params: UploadBatchParams) => void
  startRemote: (params: RemoteStartParams) => void
  resetRemote: () => void
  refreshTasks: () => Promise<void>
  isRefreshingTasks: boolean
  hasActiveTasks: boolean
  activeTaskCount: number
}

type RefreshTasksOptions = {
  notifyOnError?: boolean
  silent?: boolean
}

const INITIAL_REMOTE_STATE: RemoteUploadState = {
  phase: 'idle',
  skippedFiles: [],
  error: null,
  remoteUrl: '',
  taskId: null,
}

const RESOURCE_ADD_TASK_TYPE = 'add_resource'
const TASK_REFRESH_INTERVAL_MS = 3_000
const TASK_REFRESH_LIMIT = 50

const ResourceUploadContext =
  React.createContext<ResourceUploadContextValue | null>(null)

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function getErrorMessage(error: unknown): string {
  if (isOvClientError(error)) {
    return `${error.code}: ${error.message}`
  }
  if (error instanceof Error) {
    return error.message
  }
  return String(error)
}

function createTaskId(): string {
  if (
    typeof crypto !== 'undefined' &&
    typeof crypto.randomUUID === 'function'
  ) {
    return crypto.randomUUID()
  }
  return `upload-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

function createRemoteTaskName(url: string): string {
  const trimmed = url.trim()
  const sshMatch = trimmed.match(/^git@[^:]+:([^/]+\/[^/]+?)(?:\.git)?$/)
  if (sshMatch) {
    return sshMatch[1]
  }

  try {
    const parsed = new URL(trimmed)
    const parts = parsed.pathname.split('/').filter(Boolean)
    if (parts.length >= 2 && parsed.hostname.includes('github.com')) {
      return `${parts[0]}/${parts[1].replace(/\.git$/, '')}`
    }
    if (parts.length > 0) {
      return parts[parts.length - 1].replace(/\.git$/, '')
    }
    return parsed.hostname
  } catch {
    return trimmed
  }
}

export function useResourceUpload(): ResourceUploadContextValue {
  const context = React.useContext(ResourceUploadContext)
  if (!context) {
    throw new Error(
      'useResourceUpload must be used within ResourceUploadProvider.',
    )
  }
  return context
}

export function ResourceUploadProvider({
  children,
}: {
  children: React.ReactNode
}) {
  const [tasks, setTasks] = React.useState<ResourceUploadTask[]>([])
  const [remoteState, setRemoteState] =
    React.useState<RemoteUploadState>(INITIAL_REMOTE_STATE)
  const [isRefreshingTasks, setIsRefreshingTasks] = React.useState(false)
  const remoteAbortRef = React.useRef<AbortController | null>(null)
  const refreshInFlightRef = React.useRef(false)
  const notifiedServerTaskIdsRef = React.useRef<Set<string>>(new Set())
  const remoteCompletionCallbacksRef = React.useRef(
    new Map<string, { onCompleted?: () => void; onFailed?: () => void }>(),
  )
  const uploadQueueRef = React.useRef<Promise<void>>(Promise.resolve())

  const updateTask = React.useCallback(
    (
      taskId: string,
      updater: (task: ResourceUploadTask) => ResourceUploadTask,
    ) => {
      setTasks((prev) =>
        prev.map((task) => (task.id === taskId ? updater(task) : task)),
      )
    },
    [],
  )

  const refreshTasks = React.useCallback(
    async (options: RefreshTasksOptions = {}) => {
      if (refreshInFlightRef.current) {
        return
      }

      refreshInFlightRef.current = true
      if (!options.silent) {
        setIsRefreshingTasks(true)
      }

      try {
        const result = await getOvResult<TaskListResult>(
          getTasks({
            query: {
              limit: TASK_REFRESH_LIMIT,
              task_type: RESOURCE_ADD_TASK_TYPE,
            },
          }),
        )
        const serverTasks = normalizeTaskList(result)
        setTasks((prev) => mergeServerTasks(prev, serverTasks))
      } catch (error) {
        if (options.notifyOnError !== false) {
          toast.error(getErrorMessage(error), { duration: 5000 })
        }
      } finally {
        refreshInFlightRef.current = false
        if (!options.silent) {
          setIsRefreshingTasks(false)
        }
      }
    },
    [],
  )

  const processFileUpload = React.useCallback(
    async (
      taskId: string,
      params: UploadBatchItem,
      commonBody: ResourceImportCommonBody,
    ) => {
      try {
        updateTask(taskId, (task) => ({
          ...task,
          status: 'uploading',
          progress: 0,
        }))

        const uploadResult = await getOvResult<TempUploadResult>(
          postResourcesTempUpload({
            body: {
              file: params.file,
              telemetry: true,
            },
            onUploadProgress: (event: { loaded: number; total?: number }) => {
              const total = event.total
              if (!total) return
              updateTask(taskId, (task) => ({
                ...task,
                status: 'uploading',
                progress: Math.round((event.loaded / total) * 100),
              }))
            },
          }),
        )

        const tempFileId = isRecord(uploadResult)
          ? uploadResult.temp_file_id
          : undefined
        if (typeof tempFileId !== 'string' || !tempFileId.trim()) {
          throw new Error('Temp upload did not return temp_file_id.')
        }

        updateTask(taskId, (task) => ({
          ...task,
          status: 'processing',
          progress: null,
        }))

        const addResult = await getOvResult<ResourceImportResult>(
          postResourceImport(
            buildUploadedResourceRequest(
              tempFileId,
              params.file.name,
              commonBody,
            ),
          ),
        )

        if (addResult.status === 'error') {
          const errors = Array.isArray(addResult.errors) ? addResult.errors : []
          throw new Error(errors.join('; ') || 'Processing failed')
        }

        const rootUri =
          typeof addResult.root_uri === 'string' ? addResult.root_uri : null
        const serverTaskId =
          typeof addResult.task_id === 'string' && addResult.task_id.trim()
            ? addResult.task_id
            : null

        if (serverTaskId) {
          updateTask(taskId, (task) => ({
            ...task,
            serverTaskId,
            status: 'processing',
            progress: null,
            rootUri,
          }))
          void refreshTasks({ notifyOnError: false, silent: true })
          return
        }

        updateTask(taskId, (task) => ({
          ...task,
          status: 'success',
          progress: 100,
          finishedAt: Date.now(),
          rootUri,
        }))
        toast.success(params.file.name)
      } catch (error) {
        const { errorCode, errorMessage } = parseUploadError(
          getErrorMessage(error),
        )
        updateTask(taskId, (task) => ({
          ...task,
          status: 'failed',
          progress: null,
          finishedAt: Date.now(),
          errorCode,
          errorMessage,
        }))
        toast.error(errorMessage, { duration: 5000 })
      }
    },
    [refreshTasks, updateTask],
  )

  const enqueueUploads = React.useCallback(
    (params: UploadBatchParams) => {
      if (params.files.length === 0) return

      const createdAt = Date.now()
      const nextTasks = params.files.map((item, index) => ({
        id: createTaskId(),
        source: 'local' as const,
        serverTaskId: null,
        fileName: item.file.name,
        fileSize: item.file.size,
        fileType: item.fileType,
        status: 'pending' as const,
        progress: 0,
        createdAt: createdAt + index,
        finishedAt: null,
        errorCode: null,
        errorMessage: null,
        rootUri: null,
      }))

      setTasks((prev) => [...nextTasks, ...prev])

      for (const [index, item] of params.files.entries()) {
        const task = nextTasks[index]
        uploadQueueRef.current = uploadQueueRef.current.then(() =>
          processFileUpload(task.id, item, params.commonBody),
        )
      }
    },
    [processFileUpload],
  )

  const startRemote = React.useCallback(
    (params: RemoteStartParams) => {
      if (remoteAbortRef.current) return

      const controller = new AbortController()
      remoteAbortRef.current = controller
      const taskId = createTaskId()

      setTasks((prev) => [
        {
          id: taskId,
          source: 'remote',
          serverTaskId: null,
          fileName: createRemoteTaskName(params.url),
          fileSize: null,
          fileType: null,
          status: 'processing',
          progress: null,
          createdAt: Date.now(),
          finishedAt: null,
          errorCode: null,
          errorMessage: null,
          rootUri: null,
        },
        ...prev,
      ])

      setRemoteState({
        phase: 'processing',
        skippedFiles: [],
        error: null,
        remoteUrl: params.url,
        taskId: null,
      })

      void (async () => {
        try {
          const result = await getOvResult<ResourceImportResult>(
            postResourceImport(
              buildRemoteResourceRequest(params.url, params.commonBody),
              controller.signal,
            ),
          )

          if (result.status === 'error') {
            const errors = Array.isArray(result.errors) ? result.errors : []
            throw new Error(errors.join('; ') || 'Processing failed')
          }

          const warnings = Array.isArray(result.warnings) ? result.warnings : []
          const rootUri =
            typeof result.root_uri === 'string' ? result.root_uri : null
          const serverTaskId =
            typeof result.task_id === 'string' && result.task_id.trim()
              ? result.task_id
              : null

          params.onAccepted?.({ rootUri, taskId: serverTaskId })

          if (serverTaskId) {
            if (params.onCompleted || params.onFailed) {
              remoteCompletionCallbacksRef.current.set(serverTaskId, {
                onCompleted: params.onCompleted,
                onFailed: params.onFailed,
              })
            }
            updateTask(taskId, (task) => ({
              ...task,
              serverTaskId,
              status: 'processing',
              progress: null,
              rootUri,
            }))

            setRemoteState({
              phase: 'processing',
              skippedFiles: warnings,
              error: null,
              remoteUrl: params.url,
              taskId: serverTaskId,
            })
            void refreshTasks({ notifyOnError: false, silent: true })
            return
          }

          params.onCompleted?.()

          updateTask(taskId, (task) => ({
            ...task,
            status: 'success',
            progress: 100,
            finishedAt: Date.now(),
            rootUri,
          }))

          setRemoteState({
            phase: 'done',
            skippedFiles: warnings,
            error: null,
            remoteUrl: params.url,
            taskId: null,
          })
          toast.success(params.url)
        } catch (error) {
          if (controller.signal.aborted) {
            params.onFailed?.()
            updateTask(taskId, (task) => ({
              ...task,
              status: 'failed',
              progress: null,
              finishedAt: Date.now(),
              errorCode: 'CANCELED',
              errorMessage: 'Canceled',
            }))
            return
          }
          const message = getErrorMessage(error)
          const { errorCode, errorMessage } = parseUploadError(message)
          params.onFailed?.()

          updateTask(taskId, (task) => ({
            ...task,
            status: 'failed',
            progress: null,
            finishedAt: Date.now(),
            errorCode,
            errorMessage,
          }))

          setRemoteState({
            phase: 'idle',
            skippedFiles: [],
            error: message,
            remoteUrl: params.url,
            taskId: null,
          })
          toast.error(errorMessage, { duration: 5000 })
        } finally {
          remoteAbortRef.current = null
        }
      })()
    },
    [refreshTasks, updateTask],
  )

  const resetRemote = React.useCallback(() => {
    if (remoteAbortRef.current) {
      remoteAbortRef.current.abort()
      remoteAbortRef.current = null
    }
    setRemoteState(INITIAL_REMOTE_STATE)
  }, [])

  React.useEffect(() => {
    void refreshTasks({ notifyOnError: false, silent: true })
  }, [refreshTasks])

  const hasActiveServerTasks = React.useMemo(
    () =>
      tasks.some(
        (task) => task.serverTaskId && isUploadStatusActive(task.status),
      ),
    [tasks],
  )

  React.useEffect(() => {
    if (!hasActiveServerTasks) {
      return undefined
    }

    const interval = window.setInterval(() => {
      void refreshTasks({ notifyOnError: false, silent: true })
    }, TASK_REFRESH_INTERVAL_MS)

    return () => window.clearInterval(interval)
  }, [hasActiveServerTasks, refreshTasks])

  React.useEffect(() => {
    if (remoteState.phase !== 'processing' || !remoteState.taskId) {
      return
    }

    const remoteTask = tasks.find(
      (task) => task.serverTaskId === remoteState.taskId,
    )
    if (!remoteTask || isUploadStatusActive(remoteTask.status)) {
      return
    }

    if (remoteTask.status === 'success') {
      setRemoteState((prev) =>
        prev.taskId === remoteTask.serverTaskId
          ? { ...prev, phase: 'done', error: null }
          : prev,
      )
      return
    }

    if (remoteTask.status === 'failed') {
      setRemoteState((prev) =>
        prev.taskId === remoteTask.serverTaskId
          ? {
              ...prev,
              phase: 'idle',
              error: remoteTask.errorMessage || 'Processing failed',
            }
          : prev,
      )
      return
    }

    if (remoteTask.status === 'cancelled') {
      setRemoteState((prev) =>
        prev.taskId === remoteTask.serverTaskId
          ? {
              ...prev,
              phase: 'idle',
              error: remoteTask.errorMessage || 'Processing cancelled',
            }
          : prev,
      )
    }
  }, [remoteState.phase, remoteState.taskId, tasks])

  React.useEffect(() => {
    for (const task of tasks) {
      if (!task.serverTaskId || isUploadStatusActive(task.status)) {
        continue
      }

      const callbacks = remoteCompletionCallbacksRef.current.get(
        task.serverTaskId,
      )
      if (!callbacks) {
        continue
      }

      remoteCompletionCallbacksRef.current.delete(task.serverTaskId)
      if (task.status === 'success') {
        callbacks.onCompleted?.()
      } else {
        callbacks.onFailed?.()
      }
    }
  }, [tasks])

  React.useEffect(() => {
    for (const task of tasks) {
      if (
        !task.serverTaskId ||
        task.source === 'server' ||
        isUploadStatusActive(task.status) ||
        notifiedServerTaskIdsRef.current.has(task.serverTaskId)
      ) {
        continue
      }

      notifiedServerTaskIdsRef.current.add(task.serverTaskId)
      if (task.status === 'success') {
        toast.success(task.fileName)
      } else if (task.status === 'failed') {
        toast.error(task.errorMessage || task.fileName, { duration: 5000 })
      }
    }
  }, [tasks])

  const activeTaskCount = React.useMemo(
    () => tasks.filter((task) => isUploadStatusActive(task.status)).length,
    [tasks],
  )
  const hasActiveTasks = activeTaskCount > 0

  const value = React.useMemo<ResourceUploadContextValue>(
    () => ({
      tasks,
      remoteState,
      enqueueUploads,
      startRemote,
      resetRemote,
      refreshTasks,
      isRefreshingTasks,
      hasActiveTasks,
      activeTaskCount,
    }),
    [
      tasks,
      remoteState,
      enqueueUploads,
      startRemote,
      resetRemote,
      refreshTasks,
      isRefreshingTasks,
      hasActiveTasks,
      activeTaskCount,
    ],
  )

  return (
    <ResourceUploadContext.Provider value={value}>
      {children}
    </ResourceUploadContext.Provider>
  )
}
