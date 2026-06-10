import { useCallback, useEffect, useRef, useState } from 'react'

// Cursor movement for keyboard-driven lists. Shared by FindPalette across its
// idle / search / dirBrowse modes so the up/down/clamp logic lives once.
// `length` is read through a ref so move handlers stay referentially stable.
export function useListNavigation(length: number) {
  const [index, setIndex] = useState(0)
  const lengthRef = useRef(length)
  lengthRef.current = length

  const moveDown = useCallback(() => {
    setIndex((i) => {
      if (lengthRef.current <= 0) return 0
      return Math.min(i + 1, lengthRef.current - 1)
    })
  }, [])

  const moveUp = useCallback(() => {
    setIndex((i) => Math.max(i - 1, 0))
  }, [])

  useEffect(() => {
    setIndex((i) => {
      if (length <= 0) return 0
      return Math.min(Math.max(i, 0), length - 1)
    })
  }, [length])

  const reset = useCallback(() => {
    setIndex(0)
  }, [])

  return { index, setIndex, moveUp, moveDown, reset }
}
