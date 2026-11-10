import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { WikiDashboard } from '@openviking/react/components/wiki-dashboard';
import { getFileTypeIcon } from '@openviking/react/icons';
import { renderToStaticMarkup } from 'react-dom/server';
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
    onEdit: (...args) => events.push(['edit', ...args]),
    onDiscuss: (...args) => events.push(['discuss', ...args]),
    onOpenUri: (...args) => events.push(['open', ...args]),
  };
  const view = await mount(h(WikiDashboard, props));
  assert.equal(view.container.querySelector('.ov-wiki-dashboard__path'), null);
  for (const name of ['Manual edit', 'VikingBot discussion']) {
    await act(async () =>
      view.container.querySelector(`button[aria-label^="${name}:"]`).click(),
    );
  }
  assert.deepEqual(events, [
    ['edit', conflict],
    ['discuss', conflict],
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

test('Wiki file icons follow the shared filename mapping in lists and conflict paths', async () => {
  const names = ['handbook/policy.md', 'handbook/guide.pdf'];
  const view = await mount(
    h(WikiDashboard, { kind: 'directory', data: { files: names } }),
  );
  const shape = (svg) =>
    [...svg.querySelectorAll('path')].map((path) => path.getAttribute('d'));
  const expected = (name) => {
    const container = document.createElement('div');
    container.innerHTML = renderToStaticMarkup(h(getFileTypeIcon(name)));
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
      onEdit: (value) => events.push(['edit', value]),
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
