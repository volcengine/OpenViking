import { after } from 'node:test';
import { JSDOM } from 'jsdom';
import { act, createElement, useState } from 'react';
import { CommandConsole } from '@openviking/react/components/command-console';

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
// jsdom 没有 ResizeObserver，而快捷命令的截断测量依赖它；替身只记录目标、
// 从不回调，测试断言的是渲染结果而非尺寸变化。需要模拟尺寸的用例自行覆盖。
//
// jsdom has no ResizeObserver and the quick-command truncation measurement needs
// one; the stub records its target and never calls back, since these tests assert
// on rendered output rather than size changes. Cases that need sizes override it.
if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}
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
      CommandConsole,
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
