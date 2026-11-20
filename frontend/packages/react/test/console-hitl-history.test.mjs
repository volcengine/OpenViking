import assert from 'node:assert/strict';
import test from 'node:test';
import { act, h, mount } from './helpers/monitoring-dom.mjs';
import { runtime as r } from './helpers/monitoring.mjs';

for (const initial of ['interrupted', 'complete']) {
  test(`${initial} history stays readable without reactivating unverified HITL`, async () => {
    let props,
      terminal = initial,
      calls = 0;
    const saved = [];
    const service = {
      createSession: async () => ({ id: 'session', title: 'Session' }),
      listSessions: async () => [],
      removeSession: async () => {},
      loadMessages: async () => structuredClone(saved),
      saveMessage: async (_, message) => saved.push(structuredClone(message)),
      async *stream() {
        yield {
          type: 'tool',
          part: {
            type: 'tool',
            id: `call-${++calls}`,
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
        yield { type: terminal };
      },
    };
    function Host({ scope = 'original' }) {
      props = r.useConversation(
        { scopeKey: scope, chatOptions: { service } },
        () => {},
      );
      return null;
    }
    const send = () =>
      act(async () => props.onSend({ text: 'Create a wiki', attachments: [] }));
    const select = () => act(async () => props.sessions.onSelect('session'));
    const app = await mount(h(Host));
    try {
      await send();
      assert.equal(Boolean(props.interaction), initial === 'complete');
      if (initial === 'complete')
        await act(() =>
          props.interaction.onSubmit({ kind: 'text', value: 'My Wiki' }),
        );
      assert.ok(
        saved.some((message) =>
          message.parts.some(
            (part) => part.type === 'tool' && part.status === 'completed',
          ),
        ),
      );
      await select();
      assert.equal(props.interaction, undefined);
      assert.equal(props.messages.length, saved.length);
      if (initial === 'complete')
        assert.deepEqual(
          props.messages.find((message) => message.answerSummary).answerSummary,
          [{ label: 'Name', value: 'My Wiki' }],
        );
      await app.render(h(Host, { scope: 'fresh-controller' }));
      await select();
      assert.equal(props.interaction, undefined);
      assert.equal(props.messages.length, saved.length);
      terminal = 'complete';
      await send();
      assert.equal(props.interaction.request.kind, 'text');
    } finally {
      await app.dispose();
    }
  });
}
