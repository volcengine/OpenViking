import { groupMessages } from '@openviking/react/components/viking-bot'
import { expect, it } from 'vitest'
import { sharedMessages } from './shared-messages'
import type { Message } from './types/message'

it('keeps checkpoint boundaries through the shared message grouping', () => {
  const entry = (
    id: string,
    message_kind: Message['message_kind'],
  ): Message => ({
    id,
    turn_id: 'same-turn',
    message_kind,
    role: 'assistant',
    parts: [{ type: 'text', text: id }],
    created_at: '2026-11-11T00:00:00Z',
  })
  const messages = sharedMessages([
    entry('answer', 'assistant_step'),
    entry('continuation', 'assistant_step'),
    entry('checkpoint', 'checkpoint'),
    entry('next', 'assistant_step'),
  ])
  const rendered = groupMessages(messages)
  expect(rendered.map((message) => message.id)).toEqual([
    'answer',
    'checkpoint',
    'next',
  ])
  expect(rendered[0].parts).toHaveLength(2)
})
