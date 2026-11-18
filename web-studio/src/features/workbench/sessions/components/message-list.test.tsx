// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { MessageList } from '#/features/workbench/sessions/components/message-list'
import type { MessagePart } from '#/features/workbench/sessions/lib/types/message'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

afterEach(cleanup)

describe('MessageList streaming indicator', () => {
  it('shows the generating dot only while streaming', () => {
    const { rerender } = render(
      <MessageList messages={[]} streaming={{ iteration: 1, parts: [] }} />,
    )
    expect(screen.getByRole('status', { name: 'chat.generating' })).toBeTruthy()

    rerender(<MessageList messages={[]} />)
    expect(screen.queryByRole('status', { name: 'chat.generating' })).toBeNull()
  })
})

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

describe('MessageList streaming insert position', () => {
  it('renders streaming assistant before later user messages', () => {
    const { container } = render(
      <MessageList
        messages={[
          {
            id: 'main-input',
            role: 'user',
            parts: [{ type: 'text', text: 'A' }],
            created_at: '2026-09-13T00:00:00Z',
          },
          {
            id: 'accepted-input-b',
            role: 'user',
            parts: [{ type: 'text', text: 'B' }],
            created_at: '2026-09-13T00:00:01Z',
          },
          {
            id: 'accepted-input-c',
            role: 'user',
            parts: [{ type: 'text', text: 'C' }],
            created_at: '2026-09-13T00:00:02Z',
          },
        ]}
        streaming={{
          iteration: 1,
          parts: [{ type: 'text', text: 'answer still streaming' }],
          insertIndex: 1,
        }}
      />,
    )

    const text = container.textContent
    expect(text.indexOf('A')).toBeLessThan(
      text.indexOf('answer still streaming'),
    )
    expect(text.indexOf('answer still streaming')).toBeLessThan(
      text.indexOf('B'),
    )
    expect(text.indexOf('B')).toBeLessThan(text.indexOf('C'))
  })
})

describe('MessageList images', () => {
  it('renders an image using the server message format', () => {
    const { container } = render(
      <MessageList
        messages={[
          {
            id: 'image-message',
            role: 'user',
            created_at: '2026-09-13T00:00:00Z',
            parts: [
              {
                type: 'image_url',
                image_url: { url: 'https://example.com/a.png', detail: 'auto' },
              },
            ],
          },
        ]}
      />,
    )
    expect(container.querySelector('img')?.getAttribute('src')).toBe(
      'https://example.com/a.png',
    )
  })
})
