import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { WikiDashboard } from '@openviking/react/components/wiki-dashboard';
import { getFileTypeIcon } from '@openviking/react/icons';
import { renderMarkup } from './helpers/csr-render.mjs';
import { mount } from './helpers/module-dom.mjs';

const conflict = {
  id: 'policy',
  description: 'Approval policy differs',
  matchText: 'two approvals',
  reason: 'Two references disagree',
  severity: 'high',
  state: 'unfixed',
  beginTime: 100,
  fixTime: null,
  fixPlan: '',
  resolutionBasis: '',
  location: { status: 'missing' },
};

test('Wiki file operations work without a document URI or displayed path', async () => {
  const events = [];
  const props = {
    kind: 'file',
    data: { conflicts: [conflict] },
    defineActions: (item) =>
      ['Edit', 'Inspect'].map((name) => ({
        key: name,
        icon: h('svg', { 'data-action-icon': name }),
        tooltip: name,
        onClick: () => events.push([name, item]),
      })),
    onOpenUri: (...args) => events.push(['open', ...args]),
  };
  const view = await mount(h(WikiDashboard, props));
  assert.equal(view.container.querySelector('.ov-wiki-dashboard__path'), null);
  assert.deepEqual(
    [...view.container.querySelectorAll('[data-action-icon]')].map(
      (icon) => icon.dataset.actionIcon,
    ),
    ['Edit', 'Inspect'],
  );
  for (const name of ['Edit', 'Inspect']) {
    await act(async () =>
      view.container.querySelector(`button[aria-label="${name}"]`).click(),
    );
  }
  assert.deepEqual(events, [
    ['Edit', conflict],
    ['Inspect', conflict],
  ]);
  await view.render(
    h(WikiDashboard, {
      ...props,
      data: { ...props.data, path: 'handbook/policy.md' },
    }),
  );
  assert.match(
    view.container.querySelector('.ov-wiki-dashboard__path').textContent,
    /handbook\/policy.md/,
  );
  await act(async () =>
    view.container.querySelector('.ov-wiki-dashboard__path button').click(),
  );
  assert.equal(events.at(-1).length, 1);
  assert.deepEqual(events.at(-1), ['open']);
  await view.unmount();
});

test('Wiki actions are entirely host-owned for pending and fixed conflicts', async () => {
  const fixed = {
    ...conflict,
    id: 'fixed',
    state: 'fixed',
    fixTime: 200,
    fixPlan: 'Unified policy',
  };
  const props = { kind: 'file', data: { conflicts: [conflict, fixed] } };
  const view = await mount(h(WikiDashboard, props));
  assert.equal(
    view.container.querySelector('.ov-wiki-dashboard__actions'),
    null,
  );
  await view.render(
    h(WikiDashboard, {
      ...props,
      defineActions: (item) =>
        item.state === 'fixed'
          ? [
              {
                key: 'review',
                icon: h('svg'),
                tooltip: 'Host review',
                onClick: () => {},
              },
            ]
          : [],
    }),
  );
  const rows = view.container.querySelectorAll('.ov-wiki-dashboard__conflict');
  assert.equal(rows[0].querySelector('.ov-wiki-dashboard__actions'), null);
  assert.equal(
    rows[1].querySelectorAll('.ov-wiki-dashboard__actions button').length,
    1,
  );
  await act(async () =>
    rows[1].querySelector('.ov-wiki-dashboard__issue button').click(),
  );
  assert.equal(
    rows[1].querySelector('.ov-wiki-dashboard__evidence').hidden,
    false,
  );
  assert.match(rows[1].textContent, /Unified policy/);
  await view.render(h(WikiDashboard, { ...props, defineActions: () => [] }));
  assert.equal(
    view.container.querySelector('.ov-wiki-dashboard__actions'),
    null,
  );
  assert.equal(view.container.querySelector('[class*="--with-actions"]'), null);
  await view.unmount();
});

test('Wiki file icons follow the shared filename mapping in lists and conflict paths', async () => {
  const names = ['handbook/policy.md', 'handbook/guide.pdf'];
  const view = await mount(
    h(WikiDashboard, { kind: 'directory', data: { files: names } }),
  );
  const shape = (svg) =>
    [...svg.querySelectorAll('path')].map((path) => path.getAttribute('d'));
  const expected = (name) => {
    const container = document.createElement('div');
    container.innerHTML = renderMarkup(h(getFileTypeIcon(name)));
    return shape(container);
  };
  assert.deepEqual(
    [...view.container.querySelectorAll('.ov-wiki-dashboard__file-icon')].map(
      shape,
    ),
    names.map(expected),
  );
  await view.render(
    h(WikiDashboard, {
      kind: 'file',
      data: { conflicts: [conflict], path: names[0] },
    }),
  );
  assert.deepEqual(
    shape(view.container.querySelector('.ov-wiki-dashboard__path svg')),
    expected(names[0]),
  );
  await view.unmount();
});

test('Wiki directory callbacks identify the selected file without a current directory URI', async () => {
  const events = [];
  const file = 'handbook/policy.md';
  const view = await mount(
    h(WikiDashboard, {
      kind: 'directory',
      data: { files: [file], conflictFileCount: 1 },
      onOpenDashboard: (value) => events.push(['open', value]),
      defineActions: (value) => [
        {
          key: 'edit',
          icon: h('svg'),
          tooltip: 'Edit file',
          onClick: () => events.push(['edit', value]),
        },
      ],
    }),
  );
  for (const button of view.container.querySelectorAll('li button')) {
    await act(async () => button.click());
  }
  assert.deepEqual(events, [
    ['open', file],
    ['edit', file],
  ]);
  await view.unmount();
});
