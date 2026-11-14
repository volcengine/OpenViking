import assert from 'node:assert/strict';
import test from 'node:test';
import { act, h, mount } from './helpers/monitoring-dom.mjs';
import { runtime as r } from './helpers/monitoring.mjs';

for (const mode of ['ordinary', 'disabled', 'summary']) {
  test(`${mode} Console messages retain reply spans and link downstream render errors`, async () => {
    const events = [];
    let conversation,
      binding,
      requests = 0;
    const service = {
      createSession: async () => ({ id: 'session', title: 'Session' }),
      listSessions: async () => [],
      removeSession: async () => {},
      loadMessages: async () => [],
      async *stream() {
        if (++requests === 1 && mode === 'summary')
          yield {
            type: 'tool',
            part: {
              type: 'tool',
              id: 'call-1',
              name: 'request_user_input',
              server: 'openviking-mcp',
              status: 'completed',
              output: JSON.stringify({
                schema_version: '1.0',
                cards: [
                  { card_type: 'compile_name', title: 'Name', payload: {} },
                ],
              }),
            },
          };
        yield { type: 'complete', text: 'Done' };
      },
    };
    const MessageView = r.withMonitoring(({ fail }) => {
      if (fail) throw new Error('message render failed');
      return null;
    }, 'VikingBot');
    const Host = r.withMonitoring(({ fail }) => {
      binding = r.useMonitoringBinding();
      conversation = r.useConversation(
        {
          chatOptions: {
            service,
            hitl: mode === 'disabled' ? false : undefined,
          },
        },
        () => {},
      );
      return h(
        r.ErrorBoundary,
        null,
        h(MessageView, { messages: conversation.messages, fail }),
      );
    }, 'Console');
    const monitoring = { onEvent: (event) => events.push(event) };
    const tree = (fail = false) =>
      h(r.OpenVikingProvider, { monitoring }, h(Host, { fail }));
    const app = await mount(tree());
    try {
      await act(async () => {
        conversation.onSend({ text: 'Create a wiki', attachments: [] });
      });
      assert.equal(conversation.isSending, false);
      if (mode === 'summary') {
        await act(() =>
          conversation.interaction.onSubmit({ kind: 'text', value: 'My Wiki' }),
        );
        assert.deepEqual(
          conversation.messages.find((message) => message.answerSummary)
            .answerSummary,
          [{ label: 'Name', value: 'My Wiki' }],
        );
      }
      const spans = r.readData(binding, [conversation.messages]);
      assert.equal(spans.length, 1);
      assert.equal(spans[0].node.stage, 'console-chat-reply');
      assert.equal(spans[0].node.status, 'ready');
      await app.render(tree(true));
      const failure = events.find(
        (event) => event.name === 'viking_bot_render_error',
      );
      assert.ok(failure);
      assert.equal(failure.trace.traceId, spans[0].trace.traceId);
      assert.equal(failure.trace.parentSpanId, spans[0].trace.spanId);
    } finally {
      await app.dispose();
    }
  });
}
