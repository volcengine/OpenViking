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

test('dashboard labels inherit undefined defaults and preserve explicit empty strings', async () => {
  const view = await mount(
    h(ExperienceDashboard, {
      uri: 'id',
      kind: 'file',
      tab: 'source',
      data: { sources: [{ uri: 'source', name: 'source' }] },
      labels: { sourceCount: undefined, latest: '' },
    }),
  );
  try {
    assert.match(view.container.textContent, /Generated from 1 trajectories/);
    assert.equal(
      view.container.querySelector('summary strong').textContent,
      '',
    );
    await view.render(
      h(WikiDashboard, {
        kind: 'file',
        data: { conflicts: [] },
        labels: { title: undefined, severity: undefined, conflicts: '' },
      }),
    );
    assert.equal(
      view.container.querySelector('h2').textContent,
      'Generation results',
    );
    assert.equal(view.container.querySelector('h3').textContent, '');
    assert.match(
      view.container
        .querySelector('[aria-haspopup="listbox"]')
        .getAttribute('aria-label'),
      /Severity/,
    );
  } finally {
    await view.unmount();
  }
});

test('asynchronous test materials preserve valid experience selections and date range', async () => {
  const { act } = await import('react');
  const events = [];
  const render = (data = {}) =>
    h(ExperienceDashboard, {
      uri: 'same-resource',
      kind: 'file',
      data,
      onTabChange: (value) => events.push(value),
    });
  const view = await mount(render());
  const selectTab = (name) =>
    act(async () =>
      [...view.container.querySelectorAll('[role="tab"]')]
        .find((el) => el.textContent === name)
        .click(),
    );
  const active = () =>
    view.container.querySelector('[role="tab"][aria-selected="true"]')
      .textContent;
  try {
    const range = view.container.querySelector('select');
    await act(async () => {
      range.value = '30';
      range.dispatchEvent(new window.Event('change', { bubbles: true }));
    });
    await selectTab('Source trace');
    await view.render(render({ testMaterials: [] }));
    assert.equal(active(), 'Source trace');
    await view.render(render());
    assert.equal(active(), 'Source trace');
    assert.deepEqual(events, ['source']);
    await selectTab('Application impact');
    assert.equal(view.container.querySelector('select').value, '30');
    await view.render(render({ testMaterials: [] }));
    await selectTab('Test');
    await view.render(render());
    assert.equal(active(), 'Application impact');
    assert.equal(view.container.querySelector('select').value, '30');
    await view.render(render({ testMaterials: [] }));
    assert.equal(active(), 'Application impact');
  } finally {
    await view.unmount();
  }
});
