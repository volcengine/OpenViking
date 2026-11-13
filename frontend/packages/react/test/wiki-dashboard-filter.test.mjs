import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { WikiDashboard } from '@openviking/react/components/wiki-dashboard';
import { mount } from './helpers/module-dom.mjs';

const key = (node, value) =>
  act(async () =>
    node.dispatchEvent(
      new window.KeyboardEvent('keydown', {
        key: value,
        bubbles: true,
        cancelable: true,
      }),
    ),
  );

test('Wiki severity picker supports selection, arrows, Escape, trigger toggle and outside dismissal', async () => {
  const view = await mount(
    h(WikiDashboard, { kind: 'file', data: { conflicts: [] } }),
  );
  const trigger = view.container.querySelector('[aria-haspopup="menu"]');
  assert.equal(Boolean(view.container.querySelector('select')), false);
  await key(trigger, 'ArrowDown');
  let list = document.querySelector('[role="menu"]');
  assert.ok(list);
  assert.equal(document.activeElement.textContent, 'All');
  await key(document.activeElement, 'End');
  assert.equal(document.activeElement.textContent, 'Low');
  await act(async () => document.activeElement.click());
  assert.equal(Boolean(document.querySelector('[role="menu"]')), false);
  assert.ok(
    document.activeElement === trigger,
    'focus returns to the filter trigger',
  );
  assert.match(trigger.textContent, /Low/);
  await act(async () => trigger.click());
  assert.equal(document.activeElement.textContent, 'All');
  assert.equal(
    document.querySelector('[aria-checked="true"]').textContent,
    'Low',
  );
  await key(document.activeElement, 'End');
  await key(document.activeElement, 'ArrowUp');
  assert.equal(document.activeElement.textContent, 'Medium');
  await key(document.activeElement, 'Escape');
  assert.equal(Boolean(document.querySelector('[role="menu"]')), false);
  assert.ok(
    document.activeElement === trigger,
    'focus returns to the filter trigger',
  );
  await act(async () => trigger.click());
  await act(async () => {
    trigger.focus();
    trigger.click();
  });
  assert.equal(Boolean(document.querySelector('[role="menu"]')), false);
  await act(async () => trigger.click());
  await act(async () =>
    document.body.dispatchEvent(
      new window.MouseEvent('mousedown', { bubbles: true }),
    ),
  );
  assert.equal(Boolean(document.querySelector('[role="menu"]')), false);
  await view.unmount();
});

test('disabled Wiki actions expose their reason on focus and ignore activation', async () => {
  let calls = 0;
  const conflict = {
    id: 'c',
    matchText: 'text',
    description: 'Conflicting policy',
    reason: 'References disagree',
    severity: 'high',
    state: 'unfixed',
    beginTime: 100,
    fixTime: null,
    fixPlan: '',
    resolutionBasis: '',
    location: { status: 'missing' },
  };
  const view = await mount(
    h(WikiDashboard, {
      kind: 'file',
      data: { conflicts: [conflict] },
      defineActions: () => [
        {
          key: 'edit',
          icon: h('svg'),
          tooltip: 'Read-only document',
          disabled: true,
          onClick: () => {
            calls++;
          },
        },
        {
          key: 'missing',
          icon: h('svg'),
          tooltip: 'No handler',
        },
      ],
    }),
  );
  const button = view.container.querySelector(
    '[aria-label="Read-only document"]',
  );
  assert.equal(button.getAttribute('aria-disabled'), 'true');
  assert.equal(
    view.container
      .querySelector('[aria-label="No handler"]')
      .getAttribute('aria-disabled'),
    'true',
  );
  await act(async () => {
    button.focus();
    await new Promise((resolve) => setTimeout(resolve, 350));
  });
  assert.equal(
    document.querySelector('[role="tooltip"]').textContent,
    'Read-only document',
  );
  await act(async () => button.click());
  assert.equal(calls, 0);
  assert.equal(
    Boolean(view.container.querySelector('[aria-expanded="true"]')),
    false,
  );
  await key(button, 'Escape');
  assert.equal(Boolean(document.querySelector('[role="tooltip"]')), false);
  await view.unmount();
});
