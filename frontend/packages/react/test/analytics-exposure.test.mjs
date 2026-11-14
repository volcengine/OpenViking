import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement as h } from 'react';
import { OpenVikingProvider } from '@openviking/react/client';
import { FileBrowserActionBar } from '@openviking/react/components/file-browser-action-bar';
import { mount } from './helpers/module-dom.mjs';

test('a controlled drawer exposes once per open cycle, including retained exit DOM', async () => {
  const observers = [];
  const originalObserver = window.IntersectionObserver;
  const originalRects = HTMLElement.prototype.getClientRects;
  HTMLElement.prototype.getClientRects = () => [{ width: 100, height: 100 }];
  window.IntersectionObserver = class {
    targets = new Set();
    constructor(callback) {
      this.callback = callback;
      observers.push(this);
    }
    observe(target) {
      this.targets.add(target);
    }
    disconnect() {
      this.targets.clear();
    }
    takeRecords() {
      return [];
    }
    intersect() {
      this.callback(
        [...this.targets].map((target) => ({
          target,
          isIntersecting: true,
          intersectionRect: { width: 100, height: 100 },
        })),
      );
    }
  };
  const events = [];
  const analytics = {
    events: ['openviking_file_browser_agent_connect_drawer_show'],
    onEvent: (event) => events.push(event),
  };
  const render = (open) =>
    h(
      OpenVikingProvider,
      { analytics },
      h(FileBrowserActionBar, {
        agentConnect: { apiKey: 'sample-key' },
        agentConnectOpen: open,
      }),
    );
  const mounted = await mount(render(false));
  const expose = () => {
    for (const observer of observers) observer.intersect();
  };
  try {
    expose();
    assert.equal(events.length, 0);
    await mounted.render(render(true));
    expose();
    assert.equal(events.length, 1);
    await mounted.render(render(true));
    expose();
    assert.equal(events.length, 1);
    await mounted.render(render(false));
    expose();
    assert.equal(events.length, 1);
    await mounted.render(render(true));
    expose();
    assert.equal(events.length, 2);
    assert.equal(JSON.stringify(events).includes('sample-key'), false);
  } finally {
    await mounted.unmount();
    window.IntersectionObserver = originalObserver;
    HTMLElement.prototype.getClientRects = originalRects;
  }
});
