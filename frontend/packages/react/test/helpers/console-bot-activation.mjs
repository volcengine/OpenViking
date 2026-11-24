import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { Activity, createElement } from 'react';
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

export async function mountActivation(props, visibility) {
  let lifecycle;
  function Host(current) {
    lifecycle = useBotActivation(current);
    return null;
  }
  function element(next, mode) {
    const child = createElement(Host, next);
    return mode ? createElement(Activity, { mode }, child) : child;
  }
  const host = await mount(element(props, visibility));
  return {
    get lifecycle() {
      return lifecycle;
    },
    render: (next, mode = visibility) => host.render(element(next, mode)),
    unmount: host.unmount,
  };
}
