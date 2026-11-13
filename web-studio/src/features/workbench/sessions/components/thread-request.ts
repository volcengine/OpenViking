import { OV_PATHS } from '@openviking/react/client'
import { requestStudioModule } from '#/shared/api/module-request'

// Session ownership and product metadata stay in Studio; Console owns chat.
export function createThreadRequest({
  sessionId,
  hasTitle = false,
  onMessageSaved,
}: {
  sessionId: string
  hasTitle?: boolean
  onMessageSaved: (text?: string) => void
}): typeof fetch {
  let titled = hasTitle
  return async (input, init) => {
    const url = new URL(String(input), window.location.origin)
    const creating =
      init?.method === 'POST' && url.pathname === OV_PATHS.sessions
    const response = await requestStudioModule(
      input,
      creating
        ? { ...init, body: JSON.stringify({ session_id: sessionId }) }
        : init,
    )
    const saving =
      init?.method === 'POST' &&
      url.pathname ===
        `/api/v1/sessions/${encodeURIComponent(sessionId)}/messages`
    if (response.ok && saving) {
      const envelope = await response.clone().json()
      if (envelope.status === 'ok') {
        const message = JSON.parse(String(init.body))
        const text =
          !titled && message.role === 'user'
            ? message.parts?.find(
                (part: { type: string }) => part.type === 'text',
              )?.text
            : undefined
        if (text) titled = true
        onMessageSaved(text)
      }
    }
    return response
  }
}
