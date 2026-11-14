import { after } from 'node:test';
import { JSDOM } from 'jsdom';
import { act, createElement as h } from 'react';
const dom = new JSDOM('<!doctype html><body></body>', {
  pretendToBeVisual: true,
});
for (const key of [
  'document',
  'window',
  'HTMLElement',
  'HTMLDialogElement',
  'HTMLImageElement',
  'HTMLVideoElement',
  'Node',
  'Element',
])
  globalThis[key] = key === 'window' ? dom.window : dom.window[key];
Object.defineProperty(globalThis, 'navigator', {
  configurable: true,
  value: dom.window.navigator,
});
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
HTMLDialogElement.prototype.showModal = function () {
  this.open = true;
};
HTMLDialogElement.prototype.close = function () {
  this.open = false;
  this.dispatchEvent(new window.Event('close'));
};
const { createRoot } = await import('react-dom/client');
after(() => dom.window.close());
export { act, h };
export async function mount(tree) {
  const container = document.createElement('div');
  document.body.append(container);
  const root = createRoot(container, { onCaughtError() {} });
  const render = async (view) => {
    await act(async () => {
      root.render(view);
    });
  };
  await render(tree);
  return {
    container,
    render,
    async event(node, type) {
      await act(() =>
        node.dispatchEvent(new window.Event(type, { bubbles: true })),
      );
    },
    async dispose() {
      await act(() => root.unmount());
      container.remove();
    },
  };
}
