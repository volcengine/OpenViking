import { useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { LOADING_HINT_KEYS } from '../-constants/retrieval'

export function LoadingHint() {
  const { t } = useTranslation('retrieval')
  const [hintIndex, setHintIndex] = useState(0)

  useEffect(() => {
    const timer = setInterval(() => {
      setHintIndex((i) => (i + 1) % LOADING_HINT_KEYS.length)
    }, 1500)
    return () => clearInterval(timer)
  }, [])

  return (
    <div className="flex min-h-80 flex-col items-center justify-center gap-3">
      <Loader2 className="size-6 animate-spin text-muted-foreground/50" />
      <p
        key={hintIndex}
        className="animate-palette-in text-xs text-muted-foreground/60"
      >
        {t(LOADING_HINT_KEYS[hintIndex])}
      </p>
    </div>
  )
}
