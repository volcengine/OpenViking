import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement as h } from 'react';
import { ScopeCascade } from '@openviking/react/modules/scope-cascade';
import { mount } from './helpers/module-dom.mjs';

const option = (id) => ({ id, label: id });
test('ScopeCascade owns coordination and reports complete identities and resets', async () => {
  const changes = [];
  let loads = 0;
  const api = {
    loadLibraries: async () => {
      loads++;
      return [option('a')];
    },
    loadSpaces: async (id) => [option(id + '-space')],
    loadUsers: async (library, space) => [
      option(library + '/' + space + '/user'),
    ],
  };
  const onChange = (selection) => changes.push(selection);
  const view = await mount(h(ScopeCascade, { ...api, onChange }));
  try {
    assert.deepEqual(changes.at(-1), {
      libraryId: 'a',
      spaceId: 'a-space',
      userId: 'a/a-space/user',
    });
    assert.match(view.container.textContent, /a-space/);
    const before = loads;
    await view.render(
      h(ScopeCascade, {
        ...api,
        onChange: (selection) => changes.push(selection),
      }),
    );
    assert.equal(loads, before);
    await view.render(
      h(ScopeCascade, {
        ...api,
        loadLibraries: async () => [option('b')],
        onChange,
      }),
    );
    assert.equal(changes.at(-2), null);
    assert.deepEqual(changes.at(-1), {
      libraryId: 'b',
      spaceId: 'b-space',
      userId: 'b/b-space/user',
    });
  } finally {
    await view.unmount();
  }
});
