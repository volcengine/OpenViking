import { after } from 'node:test';
import { JSDOM } from 'jsdom';
import { act, createElement, useState } from 'react';
import { Tui } from '@openviking/react/components/tui';

const dom = new JSDOM('<!doctype html><html><body></body></html>', {
  pretendToBeVisual: true,
});
for (const name of ['window', 'document', 'Node', 'Element', 'HTMLElement'])
  globalThis[name] = name === 'window' ? dom.window : dom.window[name];
Object.defineProperty(globalThis, 'navigator', {
  configurable: true,
  value: dom.window.navigator,
});
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createRoot } = await import('react-dom/client');
after(() => dom.window.close());

export const commands = [
  {
    name: 'read',
    inputText: 'ov read',
    description: 'Read',
    usage: 'ov read <uri>',
    examples: ['ov read viking://resources/demo'],
    params: [{ label: '<uri>', description: 'URI', required: true }],
  },
];
export const record = (content, id = 'one') => ({
  id,
  command: 'ov read',
  status: 'success',
  content,
});

export async function mount(props = {}, controlled = true) {
  const container = document.createElement('div');
  document.body.append(container);
  const root = createRoot(container);
  let current = { value: '', records: [], commands, ...props };
  function Host({ input }) {
    const [value, setValue] = useState(input.value);
    return createElement(
      Tui,
      controlled ? { ...input, value, onValueChange: setValue } : input,
    );
  }
  const render = async (next = {}) => {
    current = { ...current, ...next };
    await act(() => root.render(createElement(Host, { input: current })));
  };
  await render();
  return {
    container,
    render,
    input: container.querySelector('textarea'),
    async dispose() {
      await act(() => root.unmount());
      container.remove();
    },
  };
}
export async function key(input, key, init = {}) {
  const event = new dom.window.KeyboardEvent('keydown', {
    key,
    bubbles: true,
    cancelable: true,
    ...init,
  });
  await act(() => input.dispatchEvent(event));
  return event;
}
export async function type(input, value) {
  await act(() => {
    Object.getOwnPropertyDescriptor(
      dom.window.HTMLTextAreaElement.prototype,
      'value',
    ).set.call(input, value);
    input.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
  });
}
export async function click(button) {
  await act(() => {
    button.focus();
    button.click();
  });
}
export { act, dom };
