import type {
  JsonFormatRequest,
  JsonFormatResponse,
} from '#/features/workbench/resources/lib/json-format'
import { formatJsonContent } from '#/features/workbench/resources/lib/json-format'

const workerScope = self as unknown as {
  onmessage: ((event: MessageEvent<JsonFormatRequest>) => void) | null
  postMessage: (message: JsonFormatResponse) => void
}

workerScope.onmessage = (event) => {
  try {
    workerScope.postMessage({
      content: formatJsonContent(event.data.content),
      error: null,
    })
  } catch (error) {
    workerScope.postMessage({
      content: event.data.content,
      error: error instanceof Error ? error.message : String(error),
    })
  }
}
