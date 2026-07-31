// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { MessagePart } from '#/lib/sessions/types/message'
import { MessageList } from './message-list'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

afterEach(cleanup)

const toolParts: MessagePart[] = [
  {
    type: 'tool',
    tool_id: 'tool-1',
    tool_name: 'auto_memory_search',
    tool_uri: '',
    skill_uri: '',
    tool_status: 'completed',
    tool_input: { query: 'hello' },
    tool_output: 'result',
  },
  {
    type: 'tool_result',
    tool_id: 'tool-1',
    tool_name: 'auto_memory_search',
    tool_output: 'result',
    is_error: false,
  },
]

describe('MessageList tool calls', () => {
  it.each([
    { state: 'completed', status: 'completed', isOpen: false },
    { state: 'streaming', status: 'running', isOpen: true },
  ] as const)(
    'merges tool input and result into one disclosure for $state messages',
    ({ state, status, isOpen }) => {
      const parts = toolParts.map((part) =>
        part.type === 'tool' ? { ...part, tool_status: status } : part,
      )

      render(
        <MessageList
          messages={
            state === 'completed'
              ? [
                  {
                    id: 'message-1',
                    role: 'assistant',
                    parts,
                    created_at: new Date().toISOString(),
                  },
                ]
              : []
          }
          streaming={
            state === 'streaming' ? { iteration: 1, parts } : undefined
          }
        />,
      )

      const toolName = screen.getByText('auto_memory_search')
      expect(screen.getAllByText('auto_memory_search')).toHaveLength(1)
      expect(toolName.closest('details')).toHaveProperty('open', isOpen)
      expect(toolName.closest('details')?.className).toContain('group/tool')
      expect(screen.getByText(/"query": "hello"/)).toBeTruthy()
      expect(screen.getByText('result')).toBeTruthy()
    },
  )
})

describe('MessageList reasoning', () => {
  it.each([
    { isRunning: false, label: 'chat.reasoning' },
    { isRunning: true, label: 'chat.thinking' },
  ])('renders $label as a lightweight disclosure', ({ isRunning, label }) => {
    render(
      <MessageList
        messages={
          isRunning
            ? []
            : [
                {
                  id: 'message-1',
                  role: 'assistant',
                  parts: [
                    {
                      type: 'reasoning',
                      reasoning: 'Inspect the relevant memories.',
                      is_running: false,
                    },
                  ],
                  created_at: new Date().toISOString(),
                },
              ]
        }
        streaming={
          isRunning
            ? {
                iteration: 1,
                parts: [
                  {
                    type: 'reasoning',
                    reasoning: 'Inspect the relevant memories.',
                    is_running: true,
                  },
                ],
              }
            : undefined
        }
      />,
    )

    const details = screen.getByText(label).closest('details')
    expect(details).toHaveProperty('open', isRunning)
    expect(details?.className).toContain('group/reasoning')
  })
})
