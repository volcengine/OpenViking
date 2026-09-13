import { describe, expect, it } from 'vitest'
import { groupMessages } from './group-messages'
import type { Message, MessagePart } from './types/message'

const text = (value: string): MessagePart => ({ type: 'text', text: value })
const tool = (output?: string): MessagePart => ({
  type: 'tool',
  tool_id: 't1',
  tool_name: 'exec',
  tool_uri: '',
  skill_uri: '',
  tool_input: output ? undefined : { cmd: 'ls' },
  tool_output: output,
  tool_status: output ? 'completed' : 'pending',
})
const msg = (
  id: string,
  role: Message['role'],
  parts: MessagePart[],
  extra: Partial<Message> = {},
): Message => ({
  id,
  role,
  parts,
  created_at: '2026-09-13T00:00:00Z',
  ...extra,
})
describe('server message presentation', () => {
  it('merges user-role tool transports with their calls without changing source records', () => {
    const messages = [
      msg('1', 'assistant', [text('Checking'), tool()]),
      msg('2', 'user', [tool('ok')], { message_kind: 'tool_transport' }),
      msg('3', 'assistant', [text('Done')]),
    ]
    const original = JSON.stringify(messages)
    const grouped = groupMessages(messages)
    expect(grouped).toHaveLength(1)
    expect(grouped[0].parts).toEqual([
      text('Checking'),
      { ...tool('ok'), tool_input: { cmd: 'ls' } },
      text('Done'),
    ])
    expect(JSON.stringify(messages)).toBe(original)
  })
  it('respects real user messages and explicit turn boundaries', () => {
    expect(
      groupMessages([
        msg('1', 'assistant', [text('a')], { turn_id: 'a' }),
        msg('2', 'assistant', [text('b')], { turn_id: 'b' }),
        msg('3', 'user', [text('next')]),
        msg('4', 'assistant', [text('c')]),
      ]),
    ).toHaveLength(4)
  })
  it('retains orphan legacy tools, images and context-only user messages', () => {
    const messages = [
      msg('1', 'user', [tool('ok')]),
      msg('2', 'user', [
        { type: 'image_url', image_url: { url: 'https://example.com/a.png' } },
      ]),
      msg('3', 'user', [
        {
          type: 'context',
          uri: 'viking://resources/a',
          abstract: '',
          context_type: 'resource',
        },
      ]),
    ]
    const result = groupMessages(messages)
    expect(result).toHaveLength(3)
    expect(result[0].role).toBe('assistant')
    expect(result[1].role).toBe('user')
    expect(result[2].parts).toEqual(messages[2].parts)
  })
  it('drops empty records and retains mixed user text and tools as a boundary', () => {
    expect(
      groupMessages([
        msg('1', 'user', [text(' ')]),
        msg('2', 'user', [text('question'), tool()]),
      ]),
    ).toEqual([msg('2', 'user', [text('question'), tool()])])
  })
})
