// @vitest-environment jsdom
import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MessageList } from './message-list'
import { sharedPart } from '../lib/shared-messages'
import type { Message } from '../lib/types/message'

const { list } = vi.hoisted(() => ({ list: vi.fn(() => null) }))
vi.mock('@openviking/react/components/viking-bot', () => ({
  VikingBotMessageList: list,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: 'zh-CN' },
  }),
}))
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})
const message = (id: string, role: 'assistant' | 'user'): Message => ({
  id,
  role,
  parts: [{ type: 'text', text: id }],
  created_at: '2026-11-10T00:00:00Z',
})

describe('shared message integration', () => {
  it('keeps a streaming reply before later user messages after grouping history', () => {
    render(
      <MessageList
        messages={[
          message('a', 'assistant'),
          message('b', 'assistant'),
          message('c', 'user'),
        ]}
        streaming={{
          insertIndex: 2,
          iteration: 1,
          parts: [{ type: 'text', text: 'live' }],
        }}
      />,
    )
    expect(list).toHaveBeenCalledWith(
      expect.objectContaining({
        messages: [
          expect.objectContaining({ id: 'a' }),
          expect.objectContaining({ id: 'c' }),
        ],
        streaming: {
          insertIndex: 1,
          iteration: 1,
          parts: [{ type: 'text', text: 'live' }],
        },
      }),
      undefined,
    )
  })
  it('preserves tool status, result identity, reasoning and image data', () => {
    expect(
      sharedPart({
        type: 'tool_result',
        tool_id: 't',
        tool_name: 'read',
        tool_output: 'failed',
        is_error: true,
      }),
    ).toEqual({
      type: 'tool_result',
      id: 't',
      name: 'read',
      output: 'failed',
      isError: true,
    })
    expect(
      sharedPart({
        type: 'reasoning',
        reasoning: 'thinking',
        is_running: true,
      }),
    ).toEqual({ type: 'reasoning', text: 'thinking', running: true })
    expect(
      sharedPart({
        type: 'image_url',
        image_url: { url: 'https://example.com/a.png' },
      }),
    ).toEqual({
      type: 'image',
      src: 'https://example.com/a.png',
      detail: undefined,
    })
  })
  it('forwards resource navigation to Studio', () => {
    const onResourceClick = vi.fn()
    render(<MessageList messages={[]} onResourceClick={onResourceClick} />)
    expect(list).toHaveBeenCalledWith(
      expect.objectContaining({ onOpenUri: onResourceClick }),
      undefined,
    )
  })
})
