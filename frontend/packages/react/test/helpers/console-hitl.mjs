import { act, createElement as h } from 'react';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { VikingBotInteraction } from '@openviking/react/components/viking-bot/interaction';
import { mount, settle } from './module-dom.mjs';
const { useConversation } = await import(
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'console-conversation.js'))
);

export const card = (card_type, title, payload = {}) => ({
  card_type,
  title,
  payload,
});
export const tool = (cards) => ({
  type: 'tool',
  part: {
    type: 'tool',
    id: 'call-1',
    name: 'request_user_input',
    server: 'openviking-mcp',
    status: 'completed',
    output: JSON.stringify({
      structuredContent: { schema_version: '1.0', cards },
    }),
  },
});
export const input = (node, value) =>
  act(async () => {
    const type =
      node.tagName === 'TEXTAREA'
        ? window.HTMLTextAreaElement
        : window.HTMLInputElement;
    Object.getOwnPropertyDescriptor(type.prototype, 'value').set.call(
      node,
      value,
    );
    node.dispatchEvent(new window.Event('input', { bubbles: true }));
  });
export const submit = (view) =>
  act(async () => {
    view.container
      .querySelector('form')
      .dispatchEvent(
        new window.Event('submit', { bubbles: true, cancelable: true }),
      );
  });
export const click = (node) => act(async () => node.click());
export { settle };

export async function mountHitl(
  cards,
  { terminal = 'complete', gate, hitl, onAnswer, locale = 'zh-CN' } = {},
) {
  let props;
  const requests = [];
  const saved = [];
  const service = {
    createSession: async () => ({ id: 'session', title: 'Session' }),
    listSessions: async () => [],
    removeSession: async () => {},
    loadMessages: async () => [],
    saveMessage: async (_, message) => saved.push(message),
    async *stream(request, signal) {
      requests.push(request);
      if (requests.length > 1) {
        if (onAnswer) await onAnswer(request, signal);
        yield { type: 'complete', text: 'Done' };
        return;
      }
      yield tool(cards);
      if (gate) await gate;
      if (terminal !== 'truncated')
        yield { type: terminal, text: 'Please confirm' };
    },
  };
  function Host({ scope = 'a', language = locale, controlled = false } = {}) {
    props = useConversation(
      {
        locale: language,
        scopeKey: scope,
        chatOptions: { service, hitl },
        ...(controlled ? { conversation: { messages: [] } } : {}),
      },
      () => {},
    );
    return props.interaction
      ? h(VikingBotInteraction, { ...props.interaction, locale: language })
      : null;
  }
  const view = await mount(h(Host));
  return {
    ...view,
    get props() {
      return props;
    },
    requests,
    saved,
    start: async () => {
      await act(async () =>
        props.onSend({ text: 'Create a wiki', attachments: [] }),
      );
      await settle();
    },
    rerender: (values) => view.render(h(Host, values)),
  };
}
