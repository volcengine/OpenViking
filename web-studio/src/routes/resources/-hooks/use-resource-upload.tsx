import * as React from 'react'
import { toast } from 'sonner'

import {
  getTasks,
  getOvResult,
  isOvClientError,
  postResources,
  postResourcesTempUpload,
} from '#/lib/ov-client'
import { parseUploadError } from '../-lib/upload'
import type {
  AddResourceResult,
  TempUploadResult,
} from '@ov-server/api/v1/resources'
import type { TaskListResult, TaskRecord } from '@ov-server/api/v1/tasks'

export type ResourceUploadTaskStatus =
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
  commonBody: Record<string, unknown>
}

export type RemoteStartParams = {
  url: string
  commonBody: Record<string, unknown>
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

function isTaskRecord(value: unknown): value is TaskRecord {
  return (
    isRecord(value) &&
    typeof value.task_id === 'string' &&
    typeof value.task_type === 'string' &&
    typeof value.status === 'string'
  )
}

function normalizeTaskList(value: unknown): TaskListResult {
  return Array.isArray(value) ? value.filter(isTaskRecord) : []
}

function isUploadStatusActive(status: ResourceUploadTaskStatus): boolean {
  return (
    status === 'pending' || status === 'uploading' || status === 'processing'
  )
}

function toEpochMillis(value: unknown, fallback: number): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return fallback
  }
  return value > 10_000_000_000 ? Math.round(value) : Math.round(value * 1000)
}

function getResultString(record: TaskRecord, key: string): string | null {
  const value = record.result?.[key]
  return typeof value === 'string' && value.trim() ? value : null
}

function getTaskRootUri(record: TaskRecord): string | null {
  if (typeof record.resource_id === 'string' && record.resource_id.trim()) {
    return record.resource_id
  }
  return getResultString(record, 'root_uri')
}

function getNameFromUri(uri: string): string {
  const normalized = uri.replace(/\/+$/, '')
  const parts = normalized.split('/').filter(Boolean)
  return parts[parts.length - 1] || uri
}

function getServerTaskName(record: TaskRecord): string {
  const sourceName = getResultString(record, 'source_name')
  if (sourceName) {
    return sourceName
  }

  const rootUri = getTaskRootUri(record)
  if (rootUri) {
    return getNameFromUri(rootUri)
  }

  return record.task_id
}

function toUploadStatus(
  status: TaskRecord['status'],
): ResourceUploadTaskStatus {
  if (status === 'completed') {
    return 'success'
  }
  if (status === 'failed') {
    return 'failed'
  }
  return 'processing'
}

function mergeServerTask(
  record: TaskRecord,
  existing?: ResourceUploadTask,
): ResourceUploadTask {
  const status = toUploadStatus(record.status)
  const rootUri = getTaskRootUri(record) ?? existing?.rootUri ?? null
  const createdAt =
    existing?.createdAt ?? toEpochMillis(record.created_at, Date.now())
  const updatedAt = toEpochMillis(record.updated_at, Date.now())
  const fileName =
    existing && existing.source !== 'server'
      ? existing.fileName
      : getServerTaskName(record)
  const isFinished = status === 'success' || status === 'failed'

  return {
    id: existing?.id ?? `server-${record.task_id}`,
    source: existing?.source ?? 'server',
    serverTaskId: record.task_id,
    fileName,
    fileSize: existing?.fileSize ?? null,
    fileType: existing?.fileType ?? null,
    status,
    progress: status === 'success' ? 100 : null,
    createdAt,
    finishedAt: isFinished ? (existing?.finishedAt ?? updatedAt) : null,
    errorCode:
      status === 'failed'
        ? (existing?.errorCode ?? 'SERVER_TASK_FAILED')
        : null,
    errorMessage:
      status === 'failed'
        ? record.error || existing?.errorMessage || 'Processing failed'
        : null,
    rootUri,
  }
}

function mergeServerTasks(
  previous: ResourceUploadTask[],
  serverTasks: TaskRecord[],
): ResourceUploadTask[] {
  const previousByServerId = new Map<string, ResourceUploadTask>()
  for (const task of previous) {
    if (task.serverTaskId) {
      previousByServerId.set(task.serverTaskId, task)
    }
  }

  const serverTaskIds = new Set(serverTasks.map((task) => task.task_id))
  const consumedLocalIds = new Set<string>()
  const nextTasks = serverTasks.map((record) => {
    const existing = previousByServerId.get(record.task_id)
    if (existing) {
      consumedLocalIds.add(existing.id)
    }
    return mergeServerTask(record, existing)
  })

  for (const task of previous) {
    if (consumedLocalIds.has(task.id)) {
      continue
    }
    if (
      task.source === 'server' &&
      task.serverTaskId &&
      !serverTaskIds.has(task.serverTaskId)
    ) {
      continue
    }
    nextTasks.push(task)
  }

  return nextTasks
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
      commonBody: Record<string, unknown>,
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

        const addResult = await getOvResult<AddResourceResult>(
          postResources({
            body: {
              ...commonBody,
              temp_file_id: tempFileId,
              source_name: params.file.name,
            } as Parameters<typeof postResources>[0]['body'],
          }),
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
          const result = await getOvResult<AddResourceResult>(
            postResources({
              body: {
                ...params.commonBody,
                path: params.url,
              } as Parameters<typeof postResources>[0]['body'],
              signal: controller.signal,
            }),
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

          if (serverTaskId) {
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
    }
  }, [remoteState.phase, remoteState.taskId, tasks])

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
