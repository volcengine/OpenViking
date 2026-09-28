import type { Message, MessagePart } from './types/message'

/** Presentation only: retain server records and associate tool transports within a turn. */
export function groupMessages(messages: Message[]): Message[] {
  const groups: Message[] = []
  for (const message of messages) {
    const parts = (Array.isArray(message.parts) ? message.parts : []).filter(
      (part) => part.type !== 'text' || Boolean(part.text.trim()),
    )
    if (!parts.length) continue
    const transport =
      message.message_kind === 'tool_transport' ||
      (!message.message_kind &&
        message.role === 'user' &&
        parts.every(
          (part) => part.type === 'tool' || part.type === 'tool_result',
        ))
    const role = transport ? 'assistant' : message.role
    const previous = groups.at(-1)
    const sameTurn =
      !previous?.turn_id ||
      !message.turn_id ||
      previous.turn_id === message.turn_id
    if (
      role === 'assistant' &&
      previous?.role === 'assistant' &&
      sameTurn &&
      message.message_kind !== 'checkpoint' &&
      previous.message_kind !== 'checkpoint'
    ) {
      previous.parts = mergeParts(previous.parts, parts)
      previous.turn_id ??= message.turn_id
    } else {
      groups.push({ ...message, role, parts: mergeParts([], parts) })
    }
  }
  return groups
}

function mergeParts(
  existing: MessagePart[],
  incoming: MessagePart[],
): MessagePart[] {
  const parts = [...existing]
  for (const part of incoming) {
    if (part.type === 'tool' && part.tool_id) {
      const index = parts.findIndex(
        (candidate) =>
          candidate.type === 'tool' && candidate.tool_id === part.tool_id,
      )
      if (index >= 0) {
        const previous = parts[index]
        if (previous.type === 'tool')
          parts[index] = {
            ...previous,
            ...part,
            tool_input: part.tool_input ?? previous.tool_input,
            tool_output: part.tool_output || previous.tool_output,
          }
        continue
      }
    }
    parts.push(part)
  }
  return parts
}
