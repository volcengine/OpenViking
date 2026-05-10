import * as React from 'react'
import { toast } from 'sonner'

import {
  getOvResult,
  isOvClientError,
  postResources,
  postResourcesTempUpload,
} from '#/lib/ov-client'
import { parseUploadError } from '#/routes/resources/-lib/upload'

export type ResourceUploadTaskStatus =
  | 'pending'
  | 'uploading'
  | 'processing'
  | 'success'
  | 'failed'

export type ResourceUploadTask = {
  id: string
  source: 'local' | 'remote'
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
  hasActiveTasks: boolean
  activeTaskCount: number
}

const INITIAL_REMOTE_STATE: RemoteUploadState = {
  phase: 'idle',
  skippedFiles: [],
  error: null,
  remoteUrl: '',
}

const ResourceUploadContext = React.createContext<ResourceUploadContextValue | null>(null)

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
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
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
    throw new Error('useResourceUpload must be used within ResourceUploadProvider.')
  }
  return context
}

export function ResourceUploadProvider({ children }: { children: React.ReactNode }) {
  const [tasks, setTasks] = React.useState<ResourceUploadTask[]>([])
  const [remoteState, setRemoteState] = React.useState<RemoteUploadState>(INITIAL_REMOTE_STATE)
  const remoteAbortRef = React.useRef<AbortController | null>(null)
  const uploadQueueRef = React.useRef<Promise<void>>(Promise.resolve())

  const updateTask = React.useCallback((
    taskId: string,
    updater: (task: ResourceUploadTask) => ResourceUploadTask,
  ) => {
    setTasks((prev) => prev.map((task) => (task.id === taskId ? updater(task) : task)))
  }, [])

  const processFileUpload = React.useCallback(async (
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

      const uploadResult = await getOvResult(
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

      const addResult = await getOvResult(
        postResources({
          body: {
            ...commonBody,
            temp_file_id: tempFileId,
            source_name: params.file.name,
          } as Parameters<typeof postResources>[0]['body'],
        }),
      )

      if (isRecord(addResult) && addResult.status === 'error') {
        const errors = Array.isArray(addResult.errors) ? (addResult.errors as string[]) : []
        throw new Error(errors.join('; ') || 'Processing failed')
      }

      const rootUri = isRecord(addResult) && typeof addResult.root_uri === 'string'
        ? addResult.root_uri
        : null

      updateTask(taskId, (task) => ({
        ...task,
        status: 'success',
        progress: 100,
        finishedAt: Date.now(),
        rootUri,
      }))
      toast.success(params.file.name)
    } catch (error) {
      const { errorCode, errorMessage } = parseUploadError(getErrorMessage(error))
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
  }, [updateTask])

  const enqueueUploads = React.useCallback((params: UploadBatchParams) => {
    if (params.files.length === 0) return

    const createdAt = Date.now()
    const nextTasks = params.files.map((item, index) => ({
      id: createTaskId(),
      source: 'local' as const,
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
  }, [processFileUpload])

  const startRemote = React.useCallback((params: RemoteStartParams) => {
    if (remoteAbortRef.current) return

    const controller = new AbortController()
    remoteAbortRef.current = controller
    const taskId = createTaskId()

    setTasks((prev) => [{
      id: taskId,
      source: 'remote',
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
    }, ...prev])

    setRemoteState({
      phase: 'processing',
      skippedFiles: [],
      error: null,
      remoteUrl: params.url,
    })

    void (async () => {
      try {
        const result = await getOvResult(
          postResources({
            body: {
              ...params.commonBody,
              path: params.url,
            } as Parameters<typeof postResources>[0]['body'],
            signal: controller.signal,
          }),
        )

        if (isRecord(result) && result.status === 'error') {
          const errors = Array.isArray(result.errors) ? (result.errors as string[]) : []
          throw new Error(errors.join('; ') || 'Processing failed')
        }

        const warnings = isRecord(result) && Array.isArray(result.warnings)
          ? (result.warnings as string[])
          : []
        const rootUri = isRecord(result) && typeof result.root_uri === 'string'
          ? result.root_uri
          : null

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
        })
        toast.error(errorMessage, { duration: 5000 })
      } finally {
        remoteAbortRef.current = null
      }
    })()
  }, [updateTask])

  const resetRemote = React.useCallback(() => {
    if (remoteAbortRef.current) {
      remoteAbortRef.current.abort()
      remoteAbortRef.current = null
    }
    setRemoteState(INITIAL_REMOTE_STATE)
  }, [])

  const activeTaskCount = React.useMemo(
    () => tasks.filter((task) => task.status === 'pending' || task.status === 'uploading' || task.status === 'processing').length,
    [tasks],
  )
  const hasActiveTasks = activeTaskCount > 0

  const value = React.useMemo<ResourceUploadContextValue>(() => ({
    tasks,
    remoteState,
    enqueueUploads,
    startRemote,
    resetRemote,
    hasActiveTasks,
    activeTaskCount,
  }), [
    tasks,
    remoteState,
    enqueueUploads,
    startRemote,
    resetRemote,
    hasActiveTasks,
    activeTaskCount,
  ])

  return (
    <ResourceUploadContext.Provider value={value}>
      {children}
    </ResourceUploadContext.Provider>
  )
}
