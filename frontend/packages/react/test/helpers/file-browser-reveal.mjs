import { act, createElement as h, useState, StrictMode } from 'react';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import { OpenVikingProvider } from '@openviking/react/client';
import { ok, settle } from './module-dom.mjs';

const ROOT = 'viking://resources';
const DIR = `${ROOT}/nested`;
const FILE = `${DIR}/result.png`;
const scrolled = [];
HTMLElement.prototype.scrollIntoView = function () {
  scrolled.push(this.getAttribute('data-file-tree-row-id'));
};
globalThis.CSS = { escape: (value) => value.replaceAll('"', '\\"') };
for (const name of ['HTMLIFrameElement', 'SVGElement'])
  globalThis[name] = window[name];
globalThis.getComputedStyle = window.getComputedStyle.bind(window);

function Host({ handle, controlled }) {
  const [uri, setUri] = useState('');
  return h(FileBrowser, {
    ref: handle,
    rootUri: ROOT,
    dashboardKinds: [],
    ...(controlled ? { uri, onUriChange: setUri } : {}),
  });
}
function render(fetch, handle, controlled = false, accountId) {
  return h(
    StrictMode,
    null,
    h(
      OpenVikingProvider,
      { fetch, baseURL: 'https://ov.test', accountId },
      h(Host, { handle, controlled }),
    ),
  );
}
function listing(call) {
  if (call.path === '/api/v1/fs/ls') {
    if (call.uri === ROOT)
      return ok(
        Number(call.query.offset)
          ? [{ uri: DIR, isDir: true }]
          : [{ uri: `${ROOT}/first.png` }],
        !Number(call.query.offset),
      );
    if (call.uri === DIR)
      return ok(
        Number(call.query.offset)
          ? [{ uri: FILE }]
          : [{ uri: `${DIR}/first.png` }],
        !Number(call.query.offset),
      );
  }
  if (call.path === '/api/v1/fs/stat') return ok({ isDir: call.uri === DIR });
  return ok('# Result');
}
async function command(handle, uri, options) {
  let promise;
  await act(async () => {
    promise = handle.current.openResource(uri, options);
  });
  await settle();
  return promise;
}
export { ROOT, DIR, FILE, scrolled, render, listing, command };
