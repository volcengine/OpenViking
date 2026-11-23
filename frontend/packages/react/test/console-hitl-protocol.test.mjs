import assert from 'node:assert/strict';
import test from 'node:test';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
const load = (name) =>
  import(pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, `${name}.js`)));
const { readGroup, pendingGroup, isInputTool } = await load(
  'console-hitl-protocol',
);
const { summarizeMessages } = await load('console-hitl-summary');
const cards = [{ card_type: 'compile_name', title: 'Name', payload: {} }];
const body = { schema_version: '1.0', cards };

test('MCP envelopes prefer structured content and reject malformed/unknown groups as a whole', () => {
  for (const output of [
    body,
    JSON.stringify(body),
    { structuredContent: body },
    { content: [{ type: 'text', text: JSON.stringify(body) }] },
  ]) {
    assert.equal(readGroup(output, 'call').cards.length, 1);
  }
  for (const output of [
    {
      structuredContent: { ...body, schema_version: '2.0' },
      content: [{ type: 'text', text: JSON.stringify(body) }],
    },
    { ...body, isError: true },
    { ...body, is_error: true },
    {
      ...body,
      cards: [
        ...cards,
        { card_type: 'unknown', title: 'Unknown', payload: {} },
      ],
    },
    {
      ...body,
      cards: [
        ...cards,
        {
          card_type: 'compile_schedule',
          title: 'Time',
          payload: {
            initial_value: {
              frequency: 'weekly',
              time: '12:00',
              weekday: 'invalid',
            },
          },
        },
      ],
    },
    {
      ...body,
      cards: [
        {
          card_type: 'compile_output_type',
          title: 'Type',
          payload: {
            options: [
              { value: 'a', label: 'A' },
              { value: 'a', label: 'B' },
            ],
          },
        },
      ],
    },
    { ...body, cards: [] },
    { ...body, cards: [{ ...cards[0], payload: null }] },
  ])
    assert.equal(readGroup(output, 'call'), undefined);
});

test('only the named OpenViking tool activates cards in the latest user turn', () => {
  for (const name of [
    'request_user_input',
    'openviking-mcp.request_user_input',
    'mcp_openviking-mcp_request_user_input',
  ])
    assert.equal(isInputTool({ name }), true);
  assert.equal(
    isInputTool({ name: 'request_user_input', server: 'other' }),
    false,
  );
  assert.equal(isInputTool({ name: 'other.request_user_input' }), false);
  const tool = {
    type: 'tool',
    id: 'call',
    name: 'request_user_input',
    status: 'running',
  };
  const messages = [
    { id: 'user', role: 'user', parts: [{ type: 'text', text: 'Ask' }] },
    {
      id: 'bot',
      role: 'assistant',
      parts: [
        tool,
        { type: 'tool_result', id: 'call', output: JSON.stringify(body) },
      ],
    },
  ];
  assert.equal(pendingGroup(messages).id, 'call');
  assert.equal(
    pendingGroup([...messages, { id: 'new', role: 'user', parts: [] }]),
    undefined,
  );
  assert.equal(pendingGroup([{ ...messages[1], parts: [tool] }]), undefined);
});

test('history summaries preserve wire text, resolve original option labels, and leave invalid input untouched', () => {
  const original = JSON.stringify({
    type: 'request_user_input_response',
    schema_version: '1.0',
    tool_call_id: 'call',
    answers: [
      { card_type: 'compile_output_type', title: 'Type', value: 'wiki' },
      {
        card_type: 'compile_schedule',
        title: 'Schedule',
        value: {
          frequency: 'weekly',
          weekday: 'wednesday',
          time: '12:27',
          timezone: 'UTC',
        },
      },
    ],
  });
  const messages = [
    {
      id: 'bot',
      role: 'assistant',
      parts: [
        {
          type: 'tool',
          name: 'request_user_input',
          id: 'call',
          status: 'completed',
          output: JSON.stringify({
            schema_version: '1.0',
            cards: [
              {
                card_type: 'compile_output_type',
                title: 'Type',
                payload: {
                  options: [{ label: 'Knowledge base', value: 'wiki' }],
                },
              },
              { card_type: 'compile_schedule', title: 'Schedule', payload: {} },
            ],
          }),
        },
      ],
    },
    { id: 'user', role: 'user', parts: [{ type: 'text', text: original }] },
  ];
  const result = summarizeMessages(messages, 'en-US');
  assert.equal(messages[1].answerSummary, undefined);
  assert.equal(result[1].parts[0].text, original);
  assert.equal(result[1].answerSummary[0].value, 'Knowledge base');
  assert.match(result[1].answerSummary[1].value, /Wed.*12:27 \(UTC\)/);
  for (const text of [
    'ordinary text',
    '{"type":"request_user_input_response"}',
    original.replace('compile_output_type', 'unknown_card'),
  ]) {
    const input = [
      { id: 'user', role: 'user', parts: [{ type: 'text', text }] },
    ];
    assert.equal(summarizeMessages(input, 'zh-CN')[0], input[0]);
  }
});
