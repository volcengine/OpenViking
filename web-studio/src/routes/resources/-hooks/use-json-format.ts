import { useEffect, useState } from 'react'

import JsonFormatWorker from '../-lib/json-format.worker?worker'
import type { JsonFormatRequest, JsonFormatResponse } from '../-lib/json-format'

interface JsonFormatState extends JsonFormatResponse {
  source: string
}

const EMPTY_STATE: JsonFormatState = {
  source: '',
  content: '',
  error: null,
}

export function useJsonFormat(content: string, enabled: boolean) {
  const [state, setState] = useState<JsonFormatState>(EMPTY_STATE)

  useEffect(() => {
    if (!enabled || !content) {
      return
    }

    const worker = new JsonFormatWorker()
    let active = true

    worker.onmessage = (event: MessageEvent<JsonFormatResponse>) => {
      if (!active) return
      setState({
        source: content,
        content: event.data.content,
        error: event.data.error,
      })
      worker.terminate()
    }
    worker.onerror = (event) => {
      if (!active) return
      setState({
        source: content,
        content,
        error: event.message,
      })
      worker.terminate()
    }

    const request: JsonFormatRequest = { content }
    worker.postMessage(request)

    return () => {
      active = false
      worker.terminate()
    }
  }, [content, enabled])

  const isCurrent = state.source === content
  return {
    content: isCurrent ? state.content : content,
    error: isCurrent ? state.error : null,
    isFormatting: enabled && Boolean(content) && !isCurrent,
  }
}
