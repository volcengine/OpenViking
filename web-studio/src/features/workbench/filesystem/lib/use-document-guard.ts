import { useCallback, useRef, useState } from 'react'

export function useDocumentGuard(
  currentUri: string | undefined,
  message: string,
) {
  const [dirty, setDirty] = useState(false)
  const dirtyRef = useRef(false)
  const onDirtyChange = useCallback((value: boolean) => {
    dirtyRef.current = value
    setDirty(value)
  }, [])
  const confirmNavigation = useCallback(
    (targetUri?: string) => {
      // Reselecting the current document must not discard or unlock its edits.
      if (targetUri !== undefined && targetUri === currentUri) return false
      if (dirtyRef.current && !window.confirm(message)) return false
      onDirtyChange(false)
      return true
    },
    [currentUri, message, onDirtyChange],
  )
  return { dirty, dirtyRef, onDirtyChange, confirmNavigation }
}
