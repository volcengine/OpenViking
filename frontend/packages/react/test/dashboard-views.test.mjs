import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement as h } from 'react';
import { WikiDashboard } from '@openviking/react/components/wiki-dashboard';
import { ExperienceDashboard } from '@openviking/react/components/experience-dashboard';
import { mount } from './helpers/module-dom.mjs';

test('dashboard views render supplied states without a Provider or requests', async () => {
  const original = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = () => {
    calls++;
    throw new Error('Views must not request data');
  };
  let view;
  try {
    view = await mount(h(WikiDashboard, { kind: 'file', loading: true }));
    assert.equal(
      view.container.querySelector('[aria-busy]').getAttribute('aria-busy'),
      'true',
    );
    assert.match(view.container.textContent, /Pending conflicts/);
    assert.ok(view.container.querySelector('[aria-haspopup="listbox"]'));
    await view.render(
      h(WikiDashboard, {
        kind: 'file',
        data: { conflicts: [] },
      }),
    );
    assert.match(view.container.textContent, /No matching conflicts/);
    assert.equal(view.container.querySelector('dd').textContent, '0');
    await view.render(h(WikiDashboard, { kind: 'directory', loading: true }));
    assert.match(view.container.textContent, /Conflicting files/);
    assert.equal(
      view.container.querySelector('[aria-haspopup="listbox"]'),
      null,
    );
    await view.render(
      h(ExperienceDashboard, { uri: 'experience-view', kind: 'file' }),
    );
    assert.match(view.container.textContent, /No distribution data/);
    assert.doesNotMatch(view.container.textContent, /Provider/);
    assert.equal(calls, 0);
  } finally {
    if (view) await view.unmount();
    globalThis.fetch = original;
  }
});
