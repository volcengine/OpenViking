import { useCallback, useEffect, useRef, useState } from 'react'

export function useTransientScrollbar(hideDelay = 700) {
  const [isScrolling, setIsScrolling] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const onScroll = useCallback(() => {
    setIsScrolling(true)

    if (timerRef.current) {
      clearTimeout(timerRef.current)
    }

    timerRef.current = setTimeout(() => {
      setIsScrolling(false)
      timerRef.current = null
    }, hideDelay)
  }, [hideDelay])

  useEffect(() => () => {
    if (timerRef.current) {
      clearTimeout(timerRef.current)
    }
  }, [])

  return { isScrolling, onScroll }
}
