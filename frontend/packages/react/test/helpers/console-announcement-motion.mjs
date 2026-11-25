import { act } from './command-console-dom.mjs';

export const motionStartEvents = ['transitionstart', 'animationstart'];
export const settledMotionEvents = [
  'transitionend',
  'transitioncancel',
  'animationend',
  'animationcancel',
];
export const motionEvents = [...motionStartEvents, ...settledMotionEvents];
export function motionEnvironment() {
  const prototype = window.EventTarget.prototype;
  const original = {
    request: window.requestAnimationFrame,
    cancel: window.cancelAnimationFrame,
    computed: window.getComputedStyle,
    add: prototype.addEventListener,
    remove: prototype.removeEventListener,
  };
  const frames = new Map();
  const listeners = new Set();
  let id = 0;
  let visible = false;
  window.requestAnimationFrame = (callback) => {
    frames.set(++id, callback);
    return id;
  };
  window.cancelAnimationFrame = (frame) => frames.delete(frame);
  // jsdom cannot run CSS transitions: hold computed visibility until the test
  // delivers the browser's completion event, without changing DOM attributes.
  window.getComputedStyle = (node, pseudo) => {
    const style = original.computed.call(window, node, pseudo);
    return node.classList.contains('ov-console-activation__announcement')
      ? new Proxy(style, {
          get: (target, key) =>
            key === 'visibility'
              ? visible
                ? 'visible'
                : 'hidden'
              : Reflect.get(target, key, target),
        })
      : style;
  };
  prototype.addEventListener = function (type, listener, options) {
    if (
      this.classList?.contains('ov-console-activation__announcement') &&
      motionEvents.includes(type)
    )
      listeners.add({ target: this, type, listener });
    return original.add.call(this, type, listener, options);
  };
  prototype.removeEventListener = function (type, listener, options) {
    for (const entry of listeners)
      if (
        entry.target === this &&
        entry.type === type &&
        entry.listener === listener
      )
        listeners.delete(entry);
    return original.remove.call(this, type, listener, options);
  };
  return {
    frames,
    listeners,
    setVisible: (next) => (visible = next),
    emit: (target, type) =>
      target.dispatchEvent(new window.Event(type, { bubbles: true })),
    flush: () =>
      act(() => {
        const callbacks = [...frames.values()];
        frames.clear();
        callbacks.forEach((callback) => callback(0));
      }),
    restore() {
      window.requestAnimationFrame = original.request;
      window.cancelAnimationFrame = original.cancel;
      window.getComputedStyle = original.computed;
      prototype.addEventListener = original.add;
      prototype.removeEventListener = original.remove;
    },
  };
}
