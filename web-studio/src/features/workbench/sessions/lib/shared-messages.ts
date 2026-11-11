import type {
  VikingBotMessage,
  VikingBotMessagePart,
} from '@openviking/react/components/viking-bot'
import type { Message, MessagePart } from './types/message'
import { groupMessages } from './group-messages'

export function sharedPart(part: MessagePart): VikingBotMessagePart {
  switch (part.type) {
    case 'reasoning':
      return {
        type: 'reasoning',
        text: part.reasoning,
        running: part.is_running,
      }
    case 'context':
      return {
        type: 'context',
        uri: part.uri,
        contextType: part.context_type,
        abstract: part.abstract,
      }
    case 'image_url':
      return {
        type: 'image',
        src: part.image_url.url,
        detail: part.image_url.detail,
      }
    case 'tool':
      return {
        type: 'tool',
        id: part.tool_id,
        name: part.tool_name,
        status: part.tool_status,
        input: part.tool_input,
        output: part.tool_output,
        durationMs: part.duration_ms,
      }
    case 'tool_result':
      return {
        type: 'tool_result',
        id: part.tool_id,
        name: part.tool_name,
        output: part.tool_output,
        isError: part.is_error,
      }
    case 'text':
      return {
        type: 'text',
        text: part.text
          .replace(/\n\n当前选中的上下文资源：\s*viking:\/\/[\s\S]*$/u, '')
          .replace(
            /\n\n当前用户在 Playground 中选中的上下文资源是：\s*viking:\/\/[\s\S]*$/u,
            '',
          ),
      }
    case 'iteration':
      return part
  }
}

export function sharedMessages(messages: Message[]): VikingBotMessage[] {
  return groupMessages(messages).map((message) => ({
    id: message.id,
    role: message.role,
    // Studio already groups protocol records, including checkpoint boundaries.
    turnId: message.id,
    createdAt: message.created_at,
    parts: message.parts.map(sharedPart),
  }))
}
