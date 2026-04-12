import * as React from 'react'
import { toast } from 'sonner'

import {
  getOvResult,
  isOvClientError,
  postResources,
  postResourcesTempUpload,
} from '#/lib/ov-client'

export type UploadPhase = 'idle' | 'uploading' | 'processing' | 'done'

export type UploadState = {
  phase: UploadPhase
  progress: number
  skippedFiles: string[]
  error: string | null
  fileName: string | null
  fileSize: number | null
  fileType: string | null
  remoteUrl: string
  mode: 'upload' | 'remote'
}

export type UploadStartParams = {
  file: File
  fileType: string | null
  commonBody: Record<string, unknown>
}

export type RemoteStartParams = {
  url: string
  commonBody: Record<string, unknown>
}

type ResourceUploadContextValue = {
  state: UploadState
  startUpload: (params: UploadStartParams) => void
  startRemote: (params: RemoteStartParams) => void
  reset: () => void
  isActive: boolean
}

const INITIAL_STATE: UploadState = {
  phase: 'idle',
  progress: 0,
  skippedFiles: [],
  error: null,
  fileName: null,
  fileSize: null,
  fileType: null,
  remoteUrl: '',
  mode: 'upload',
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

export function useResourceUpload(): ResourceUploadContextValue {
  const context = React.useContext(ResourceUploadContext)
  if (!context) {
    throw new Error('useResourceUpload must be used within ResourceUploadProvider.')
  }
  return context
}

export function ResourceUploadProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = React.useState<UploadState>(INITIAL_STATE)
  const abortRef = React.useRef<AbortController | null>(null)

  const startUpload = React.useCallback((params: UploadStartParams) => {
    if (abortRef.current) return

    const controller = new AbortController()
    abortRef.current = controller

    setState({
      phase: 'uploading',
      progress: 0,
      skippedFiles: [],
      error: null,
      fileName: params.file.name,
      fileSize: params.file.size,
      fileType: params.fileType,
      remoteUrl: '',
      mode: 'upload',
    })

    void (async () => {
      try {
        const uploadResult = await getOvResult(
          postResourcesTempUpload({
            body: {
              file: params.file,
              telemetry: true,
            },
            onUploadProgress: (event: { loaded: number; total?: number }) => {
              if (event.total) {
                setState(prev => ({
                  ...prev,
                  progress: Math.round((event.loaded / event.total!) * 100),
                }))
              }
            },
            signal: controller.signal,
          }),
        )

        const tempFileId = isRecord(uploadResult)
          ? uploadResult.temp_file_id
          : undefined
        if (typeof tempFileId !== 'string' || !tempFileId.trim()) {
          throw new Error('Temp upload did not return temp_file_id.')
        }

        setState(prev => ({ ...prev, phase: 'processing' }))

        const addResult = await getOvResult(
          postResources({
            body: {
              ...params.commonBody,
              temp_file_id: tempFileId,
              source_name: params.file.name,
            } as Parameters<typeof postResources>[0]['body'],
            signal: controller.signal,
          }),
        )

        // Check for processing errors
        if (isRecord(addResult) && addResult.status === 'error') {
          const errors = Array.isArray(addResult.errors) ? (addResult.errors as string[]) : []
          throw new Error(errors.join('; ') || 'Processing failed')
        }

        const warnings = isRecord(addResult) && Array.isArray(addResult.warnings)
          ? (addResult.warnings as string[])
          : []

        setState(prev => ({
          ...prev,
          phase: 'done',
          progress: 100,
          skippedFiles: warnings,
        }))
        toast.success(params.file.name)
      } catch (err) {
        if (controller.signal.aborted) return
        const message = getErrorMessage(err)
        setState(prev => ({
          ...prev,
          phase: 'idle',
          error: message,
        }))
        toast.error(message, { duration: 5000 })
      } finally {
        abortRef.current = null
      }
    })()
  }, [])

  const startRemote = React.useCallback((params: RemoteStartParams) => {
    if (abortRef.current) return

    const controller = new AbortController()
    abortRef.current = controller

    setState({
      phase: 'processing',
      progress: 0,
      skippedFiles: [],
      error: null,
      fileName: null,
      fileSize: null,
      fileType: null,
      remoteUrl: params.url,
      mode: 'remote',
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

        // Check for processing errors
        if (isRecord(result) && result.status === 'error') {
          const errors = Array.isArray(result.errors) ? (result.errors as string[]) : []
          throw new Error(errors.join('; ') || 'Processing failed')
        }

        const warnings = isRecord(result) && Array.isArray(result.warnings)
          ? (result.warnings as string[])
          : []

        setState(prev => ({
          ...prev,
          phase: 'done',
          skippedFiles: warnings,
        }))
        toast.success(params.url)
      } catch (err) {
        if (controller.signal.aborted) return
        const message = getErrorMessage(err)
        setState(prev => ({
          ...prev,
          phase: 'idle',
          error: message,
        }))
        toast.error(message, { duration: 5000 })
      } finally {
        abortRef.current = null
      }
    })()
  }, [])

  const reset = React.useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort()
      abortRef.current = null
    }
    setState(INITIAL_STATE)
  }, [])

  const isActive = state.phase === 'uploading' || state.phase === 'processing'

  const value = React.useMemo<ResourceUploadContextValue>(() => ({
    state, startUpload, startRemote, reset, isActive,
  }), [state, startUpload, startRemote, reset, isActive])

  return (
    <ResourceUploadContext.Provider value={value}>
      {children}
    </ResourceUploadContext.Provider>
  )
}
