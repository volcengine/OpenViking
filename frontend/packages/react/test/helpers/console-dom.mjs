import { createElement, StrictMode } from 'react';
import { OpenVikingProvider } from '@openviking/react/client';
import { Console } from '@openviking/react/modules/console';
import { act, type, key, click } from './command-console-dom.mjs';
const { createRoot } = await import('react-dom/client');
globalThis.HTMLButtonElement = window.HTMLButtonElement;
globalThis.MutationObserver = window.MutationObserver;
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};
export const ok = (result) => Response.json({ status: 'ok', result });
export async function mount(transport, props = {}, provider = {}) {
  const container = document.createElement('div');
  document.body.append(container);
  const root = createRoot(container);
  let disposed = false;
  async function render(next = props, config = provider) {
    await act(() =>
      root.render(
        createElement(
          StrictMode,
          null,
          createElement(
            OpenVikingProvider,
            {
              baseURL: 'https://demo.invalid/gateway',
              fetch: transport,
              ...config,
            },
            createElement(Console, { defaultMode: 'command', ...next }),
          ),
        ),
      ),
    );
  }
  await render();
  const button = (name) =>
    [...container.querySelectorAll('button')].find(
      (element) =>
        element.getAttribute('aria-label') === name ||
        element.textContent === name,
    );
  return {
    container,
    render,
    button,
    get input() {
      return container.querySelector('textarea');
    },
    get records() {
      return [...container.querySelectorAll('.ov-command-console__record')];
    },
    get text() {
      return container.textContent;
    },
    press: (name) => click(button(name)),
    async execute(command) {
      await type(container.querySelector('textarea'), command);
      await key(container.querySelector('textarea'), 'Enter', {
        ctrlKey: true,
      });
    },
    async dispose() {
      if (disposed) return;
      disposed = true;
      await act(() => root.unmount());
      container.remove();
    },
  };
}
