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
    '请用10个字以内为以下对话生成一个简短标题，只返回标题本身，不要加引号或其他标点：',
    `用户：${userMessage.slice(0, 200)}`,
    `助手：${assistantReply.slice(0, 300)}`,
  ].join('\n')

  const res = await sendChat({
    message: prompt,
    need_reply: true,
  })

  return res.message.trim().replace(/^["'""]|["'""]$/g, '')
}
