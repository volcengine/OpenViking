import { sendChat } from './api'

/**
 * Ask the bot to generate a short title for a conversation.
 * Uses a non-streaming call without session_id to avoid polluting history.
 */
export async function generateTitle(
  userMessage: string,
  assistantReply: string,
): Promise<string> {
  const prompt = [
    'Create a concise conversation title in the same language as the conversation.',
    'Return only the title, without quotes or trailing punctuation.',
    'Keep it under 10 Chinese characters or 6 English words.',
    `User: ${userMessage.slice(0, 200)}`,
    `Assistant: ${assistantReply.slice(0, 300)}`,
  ].join('\n')

  const res = await sendChat({
    message: prompt,
    need_reply: true,
  })

  return res.message.trim().replace(/^["'""]|["'""]$/g, '')
}
