import assert from 'node:assert/strict';
import test from 'node:test';
import {
  act,
  button,
  click,
  fixture,
  mount,
  open,
  tree,
} from './helpers/acl-dom.mjs';

const waitForDirectory = () =>
  act(async () => new Promise((resolve) => setTimeout(resolve, 240)));
async function type(value) {
  const input = document.querySelector('.ov-file-browser-acl__add input');
  await act(async () => {
    Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      'value',
    ).set.call(input, value);
    input.dispatchEvent(new window.Event('input', { bubbles: true }));
  });
  await waitForDirectory();
}

test('principal pagination resets for query changes, A-B-A changes, and reopening', async () => {
  const calls = [];
  const view = await mount(
    tree(fixture(), {
      acl: {
        listPrincipals: async (query) => {
          calls.push(query);
          return {
            principals: [`user:carol-${query.page}`],
            hasMore: query.page === 1,
          };
        },
      },
    }),
  );
  try {
    await open();
    const input = document.querySelector('.ov-file-browser-acl__add input');
    assert.equal(document.activeElement, input);
    await waitForDirectory();
    assert.equal(input.getAttribute('aria-expanded'), 'false');
    assert.equal(calls.length, 0);
    await click(input);
    await waitForDirectory();
    assert.equal(input.getAttribute('aria-expanded'), 'true');
    await click(input);
    assert.equal(input.getAttribute('aria-expanded'), 'true');
    assert.equal(calls.at(-1).page, 1);
    await click(button('Load more'));
    await waitForDirectory();
    assert.equal(calls.at(-1).page, 2);
    await type('carol');
    assert.equal(calls.at(-1).page, 1);
    assert.equal(calls.at(-1).keyword, 'carol');
    await type('');
    assert.equal(calls.at(-1).page, 1);
    await click(button('Load more'));
    await waitForDirectory();
    assert.equal(calls.at(-1).page, 2);
    await click(button('Cancel'));
    await click(input);
    await waitForDirectory();
    assert.equal(input.getAttribute('aria-expanded'), 'true');
    assert.equal(calls.at(-1).page, 1);
    assert.equal(calls.length, 6);
  } finally {
    await view.unmount();
  }
});

test('host picker limits new grants without hiding existing Group grants', async () => {
  const fetch = fixture();
  const view = await mount(
    tree(fetch, { acl: { supportedPrincipalTypes: ['user'] } }),
  );
  try {
    await open();
    await click(document.querySelector('.ov-file-browser-acl__add input'));
    assert.equal(button('Group'), undefined);
    assert.ok(
      document.querySelector('button[aria-label="group:research: Can read"]'),
    );
    assert.equal(fetch.countOf('/api/v1/admin/accounts/demo/groups'), 0);
  } finally {
    await view.unmount();
  }
});
