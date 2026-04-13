import { useCallback, useRef, useState } from 'react'

import { getOvResult, postResourcesTempUpload } from '#/lib/ov-client'

export type AttachmentPhase = 'idle' | 'uploading' | 'ready' | 'error'

export interface FileAttachment {
  fileName: string
  fileSize: number
  tempFileId: string | null
  phase: AttachmentPhase
  progress: number
  error: string | null
}

const INITIAL: FileAttachment = {
  fileName: '',
  fileSize: 0,
  tempFileId: null,
  phase: 'idle',
  progress: 0,
  error: null,
}

export function useFileAttachment() {
  const [attachment, setAttachment] = useState<FileAttachment>(INITIAL)
  const abortRef = useRef<AbortController | null>(null)

  const attach = useCallback((file: File) => {
    // Cancel any in-progress upload
    abortRef.current?.abort()

    const controller = new AbortController()
    abortRef.current = controller

    setAttachment({
      fileName: file.name,
      fileSize: file.size,
      tempFileId: null,
      phase: 'uploading',
      progress: 0,
      error: null,
    })

    void (async () => {
      try {
        const result = await getOvResult(
          postResourcesTempUpload({
            body: { file, telemetry: true },
            onUploadProgress: (event: { loaded: number; total?: number }) => {
              if (event.total) {
                setAttachment((prev) => ({
                  ...prev,
                  progress: Math.round((event.loaded / event.total!) * 100),
                }))
              }
            },
            signal: controller.signal,
          }),
        )

        const tempFileId =
          result && typeof result === 'object' && 'temp_file_id' in result
            ? (result as { temp_file_id: string }).temp_file_id
            : undefined

        if (typeof tempFileId !== 'string' || !tempFileId.trim()) {
          throw new Error('Upload did not return temp_file_id')
        }

        setAttachment((prev) => ({
          ...prev,
          tempFileId,
          phase: 'ready',
          progress: 100,
        }))
      } catch (err) {
        if (controller.signal.aborted) return
        setAttachment((prev) => ({
          ...prev,
          phase: 'error',
          error: err instanceof Error ? err.message : String(err),
        }))
      } finally {
        abortRef.current = null
      }
    })()
  }, [])

  const clear = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    setAttachment(INITIAL)
  }, [])

  return { attachment, attach, clear }
}
