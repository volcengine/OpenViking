import i18n from '#/i18n/core'
import { OvClientError } from '#/lib/ov-client/errors'
import type { Message } from './types/message'

function invalidMessage(location: string): never {
  throw new OvClientError({
    code: 'INVALID_SESSION_MESSAGE',
    message: i18n.t('studio:errors.invalidMessage', { location }),
  })
}

/** Both API and persisted JSONL messages cross the same validation seam. */
export function parseSessionMessages(
  value: unknown,
  location = i18n.t('studio:errors.sessionMessages'),
): Message[] {
  if (value === undefined) return []
  if (!Array.isArray(value)) invalidMessage(location)
  return value.flatMap((message: unknown, index): Message[] => {
    const at = i18n.t('studio:errors.messageLocation', { location, index: index + 1 })
    if (
      !message ||
      typeof message !== 'object' ||
      !('id' in message) ||
      typeof message.id !== 'string' ||
      !('role' in message) ||
      (message.role !== 'user' &&
        message.role !== 'assistant' &&
        message.role !== 'system') ||
      !('parts' in message) ||
      !Array.isArray(message.parts) ||
      message.parts.some((part: unknown) => {
        if (
          !part ||
          typeof part !== 'object' ||
          !('type' in part) ||
          typeof part.type !== 'string'
        )
          return true
        if (part.type === 'text')
          return !('text' in part) || typeof part.text !== 'string'
        if (part.type === 'reasoning')
          return !('reasoning' in part) || typeof part.reasoning !== 'string'
        return false
      })
    )
      invalidMessage(at)
    // System records are valid session history, but this UI only renders
    // user and assistant messages. Never present a system prompt as an answer.
    if (message.role === 'system') return []
    return [
      {
        ...message,
        created_at:
          'created_at' in message && typeof message.created_at === 'string'
            ? message.created_at
            : '',
      } as Message,
    ]
  })
}

export function parseSessionArchive(
  value: unknown,
  archiveId: string,
): Message[] {
  if (typeof value !== 'string') {
    throw new OvClientError({
      code: 'INVALID_SESSION_ARCHIVE',
      message: i18n.t('studio:errors.invalidJsonl', { archiveId }),
    })
  }
  return value.split(/\r?\n/).flatMap((line, index) => {
    if (!line.trim()) return []
    const at = i18n.t('studio:errors.archiveLine', { archiveId, index: index + 1 })
    let message: unknown
    try {
      message = JSON.parse(line)
    } catch {
      throw new OvClientError({
        code: 'INVALID_SESSION_ARCHIVE',
        message: i18n.t('studio:errors.invalidJson', { location: at }),
      })
    }
    return parseSessionMessages([message], at)
  })
}
