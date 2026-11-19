import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createElement } from 'react';
import { mount } from './module-dom.mjs';

const { useBotActivation } = await import(
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'console-bot-activation.js'))
);

export function deferred() {
  let resolve;
  const promise = new Promise((success) => {
    resolve = success;
  });
  return { promise, resolve };
}

export async function mountActivation(props) {
  let lifecycle;
  function Host(current) {
    lifecycle = useBotActivation(current);
    return null;
  }
  const host = await mount(createElement(Host, props));
  return {
    get lifecycle() {
      return lifecycle;
    },
    render: (next) => host.render(createElement(Host, next)),
    unmount: host.unmount,
  };
}
