import { after } from 'node:test';
import { JSDOM } from 'jsdom';

// Component checks run against browser DOM semantics; render lifecycle stays active.
if (typeof document === 'undefined') {
  const dom = new JSDOM('<!doctype html><html><body></body></html>', {
    pretendToBeVisual: true,
    url: 'http://localhost/',
  });
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  for (const name of [
    'Node',
    'Element',
    'HTMLElement',
    'HTMLInputElement',
    'HTMLTextAreaElement',
    'HTMLButtonElement',
    'HTMLImageElement',
    'HTMLVideoElement',
    'HTMLDialogElement',
    'SVGElement',
    'MutationObserver',
    'Document',
    'DocumentFragment',
    'ShadowRoot',
  ])
    globalThis[name] = dom.window[name];
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: dom.window.navigator,
  });
  globalThis.getComputedStyle = (node) => window.getComputedStyle(node);
  globalThis.requestAnimationFrame = (callback) =>
    window.requestAnimationFrame(callback);
  globalThis.cancelAnimationFrame = (id) => window.cancelAnimationFrame(id);
  after(() => dom.window.close());
}

// jsdom has no layout observer; element lifecycle remains real while geometry tests supply measurements.
if (!globalThis.ResizeObserver)
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
