import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { VikingBotInteraction } from '@openviking/react/components/viking-bot/interaction';
import { mount, settle } from './helpers/module-dom.mjs';

const folder = (uri = 'viking://resources/wiki') => ({
  kind: 'entry',
  type: 'directory',
  id: uri,
  uri,
  name: uri.split('/').at(-1),
  depth: 0,
  hasChildren: false,
});
const read = async (uri) => ({ uri, isDirectory: true, canWrite: true });
const click = (node) =>
  act(async () => {
    node.focus();
    node.click();
  });
const modal = () => document.querySelector('.ov-context-picker [role=dialog]');
const button = (root, name) =>
  [...root.querySelectorAll('button')].find(
    (node) => node.textContent === name,
  );
const trigger = (view) =>
  view.container.querySelector('.ov-viking-bot__directory-trigger');
const submit = (view) =>
  act(async () =>
    view.container
      .querySelector('form')
      .dispatchEvent(
        new window.Event('submit', { bubbles: true, cancelable: true }),
      ),
  );
const request = (picker = {}) => ({
  kind: 'target',
  initialUri: folder().uri,
  directoryPicker: {
    rows: [folder()],
    permission: {
      uri: folder().uri,
      info: { uri: folder().uri, isDirectory: true, canWrite: true },
    },
    operations: { readDirectory: read },
    ...picker,
  },
});

test('late directory rows restore selection; search is host-controlled and cancelling keeps the target', async () => {
  let query;
  const render = (rows, isReady, search = '') =>
    h(VikingBotInteraction, {
      request: request({
        rows,
        isReady,
        query: search,
        onQueryChange: (value) => {
          query = value;
        },
        operations: {},
      }),
    });
  const view = await mount(render([], false));
  try {
    await click(trigger(view));
    assert.equal(button(modal(), 'Use this directory').disabled, true);
    await view.render(render([folder()], true));
    assert.equal(
      modal().querySelector('[role=treeitem]').getAttribute('aria-selected'),
      'true',
    );
    assert.equal(button(modal(), 'Use this directory').disabled, false);
    const input = modal().querySelector('input[type=search]');
    await act(async () => {
      Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value',
      ).set.call(input, 'archive');
      input.dispatchEvent(new window.Event('input', { bubbles: true }));
    });
    assert.equal(query, 'archive');
    await view.render(
      render([folder('viking://resources/archive')], true, query),
    );
    await click(modal().querySelector('[role=treeitem]'));
    await click(button(modal(), 'Cancel'));
    await settle();
    assert.match(trigger(view).textContent, /resources\/wiki/);
    assert.equal(document.activeElement, trigger(view));
  } finally {
    await view.unmount();
  }
});

test('target delegates validation to the host, preserves the URI on rejection, and prevents duplicate submissions', async () => {
  let writable = true;
  let resolve;
  const answers = [];
  let checking = false;
  const readDirectory = async (uri) => {
    if (checking)
      await new Promise((done) => {
        resolve = done;
      });
    return { uri, isDirectory: true, canWrite: writable };
  };
  const view = await mount(
    h(VikingBotInteraction, {
      request: request({ operations: { readDirectory } }),
      onSubmit: async (value) => {
        const info = await readDirectory(value.value);
        if (!info.canWrite) throw new Error('Directory is read-only');
        answers.push(value);
      },
    }),
  );
  try {
    writable = false;
    await submit(view);
    assert.equal(answers.length, 0);
    assert.match(
      view.container.querySelector('[role=alert]').textContent,
      /read-only/,
    );
    assert.match(trigger(view).textContent, /resources\/wiki/);
    writable = true;
    checking = true;
    await submit(view);
    await submit(view);
    assert.equal(answers.length, 0);
    await act(async () => resolve());
    assert.deepEqual(answers, [{ kind: 'target', value: folder().uri }]);
  } finally {
    await view.unmount();
  }
});

test('child creation validates names, preserves input after failure, and accepts success independently of refresh', async () => {
  let fail = true;
  let calls = 0;
  const operations = {
    readDirectory: read,
    createDirectory: async (parent, name) => {
      calls++;
      if (fail) throw new Error('Create rejected');
      return `${parent}/${name}`;
    },
    refresh: () => Promise.reject(new Error('Refresh rejected')),
  };
  const view = await mount(
    h(VikingBotInteraction, { request: request({ operations }) }),
  );
  try {
    await click(trigger(view));
    await click(button(modal(), 'New subdirectory'));
    const form = document.querySelector(
      '.ov-viking-bot__create-directory form',
    );
    const input = form.querySelector('input');
    const fill = (value) =>
      act(async () => {
        Object.getOwnPropertyDescriptor(
          window.HTMLInputElement.prototype,
          'value',
        ).set.call(input, value);
        input.dispatchEvent(new window.Event('input', { bubbles: true }));
      });
    const create = () =>
      act(async () =>
        form.dispatchEvent(
          new window.Event('submit', { bubbles: true, cancelable: true }),
        ),
      );
    await fill('../bad');
    await create();
    assert.equal(calls, 0);
    assert.match(
      form.querySelector('[role=alert]').textContent,
      /one directory name/,
    );
    await fill('notes');
    await create();
    assert.equal(input.value, 'notes');
    assert.equal(
      form.querySelector('[role=alert]').textContent,
      'Create rejected',
    );
    fail = false;
    await create();
    await settle();
    assert.equal(modal(), null);
    assert.match(trigger(view).textContent, /wiki\/notes/);
    assert.equal(view.container.querySelector('[role=alert]'), null);
  } finally {
    await view.unmount();
  }
});
