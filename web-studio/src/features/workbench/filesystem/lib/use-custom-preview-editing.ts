import type {
  FilePreviewEditing,
  FilePreviewSource,
} from '@openviking/react/components/file-preview'
import { useEffect, useRef, useState } from 'react'

type EditState = {
  scope: string
  draft?: string
  error?: 'editFailed' | 'saveFailed'
  busy: boolean
}

/** Manages the editor for Studio-owned content tabs. Native file tabs use FilePreview's editor. */
export function useCustomPreviewEditing(
  scope: string,
  source: FilePreviewSource,
  options: FilePreviewEditing | undefined,
  onDirtyChange: (dirty: boolean) => void,
) {
  const [state, setState] = useState<EditState>()
  const active = state?.scope === scope ? state : undefined
  const pending = useRef<object | null>(null)

  // Drop requests when the preview scope changes or the editor unmounts.
  useEffect(
    () => () => {
      pending.current = null
    },
    [scope],
  )

  const startEditing = async () => {
    if (!options || pending.current) return
    const request = {}
    pending.current = request
    setState({ scope, busy: true })
    try {
      const draft = await options.loadContent?.(source)
      if (pending.current !== request) return
      setState(draft === undefined ? undefined : { scope, draft, busy: false })
      if (draft !== undefined) onDirtyChange(true)
    } catch {
      if (pending.current === request)
        setState({ scope, error: 'editFailed', busy: false })
    } finally {
      if (pending.current === request) pending.current = null
    }
  }

  const saveEditing = async () => {
    const draft = active?.draft
    if (!options || draft === undefined || pending.current) return false
    const request = {}
    pending.current = request
    setState({ scope, draft, busy: true })
    try {
      await options.save(draft)
      if (pending.current !== request) return false
      setState(undefined)
      options.onSave?.(draft)
      return true
    } catch {
      if (pending.current === request)
        setState({ scope, draft, error: 'saveFailed', busy: false })
      return false
    } finally {
      if (pending.current === request) pending.current = null
    }
  }

  const cancelEditing = () => {
    if (pending.current) return
    setState(undefined)
    if (active?.draft !== undefined) options?.onCancel?.(active.draft)
  }

  return {
    draft: active?.draft,
    error: active?.error,
    busy: active?.busy ?? false,
    editing: active?.draft !== undefined,
    canEdit: Boolean(options),
    startEditing,
    saveEditing,
    cancelEditing,
    changeDraft: (draft: string) => {
      if (!pending.current) setState({ scope, draft, busy: false })
    },
  }
}
