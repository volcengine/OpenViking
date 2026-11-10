import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import {
  OpenVikingProvider,
  WikiDashboardPane,
} from './helpers/internal-hooks.mjs';
import { mount, ok, recordingFetch } from './helpers/module-dom.mjs';

test('Wiki module binds operation URIs and resets local filters when the document changes', async () => {
  const record = {
    match_text: 'two approvals',
    problem_description: 'Approval policy differs',
    reason: 'Two references disagree',
    severity: 'high',
    state: 'unfixed',
    begin_time: 100,
    fix_time: null,
    fix_plan: '',
    resolution_basis: '',
  };
  const fetch = recordingFetch(({ path }) =>
    path.endsWith('/stat')
      ? ok({ isDir: false })
      : ok(
          `two approvals\n<!-- CONFLICTS_CONTENT\n${JSON.stringify(record)}\n-->`,
        ),
  );
  const events = [];
  const render = (uri) =>
    h(
      OpenVikingProvider,
      { fetch },
      h(WikiDashboardPane, {
        uri,
        kind: 'file',
        onEdit: (...args) => events.push(['edit', ...args]),
        onDiscuss: (...args) => events.push(['discuss', ...args]),
        onOpenUri: (...args) => events.push(['open', ...args]),
      }),
    );
  const first = 'viking://user/alice/resources/wiki/first.md';
  const next = first.replace('first.md', 'next.md');
  const view = await mount(render(first));
  for (const name of ['Manual edit', 'VikingBot discussion']) {
    await act(async () =>
      view.container.querySelector(`button[aria-label^="${name}:"]`).click(),
    );
  }
  assert.deepEqual(
    events.map(([action, uri, conflict]) => [
      action,
      uri,
      conflict.description,
    ]),
    [
      ['edit', first, record.problem_description],
      ['discuss', first, record.problem_description],
    ],
  );
  await act(async () =>
    view.container.querySelector('[aria-haspopup="listbox"]').click(),
  );
  await act(async () =>
    [...document.querySelectorAll('[role="option"]')]
      .find((el) => el.textContent === 'Low')
      .click(),
  );
  assert.match(view.container.textContent, /No matching conflicts/);
  await view.render(render(next));
  assert.match(
    view.container.querySelector('[aria-haspopup="listbox"]').textContent,
    /All/,
  );
  assert.match(view.container.textContent, /Approval policy differs/);
  await act(async () =>
    view.container.querySelector('.ov-wiki-dashboard__path button').click(),
  );
  assert.deepEqual(events.at(-1), ['open', next]);
  await view.unmount();
});
