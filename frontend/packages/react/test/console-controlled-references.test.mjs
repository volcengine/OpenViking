import assert from 'node:assert/strict';
import test from 'node:test';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { act, createElement as h, useState } from 'react';
import {
  deferred,
  mount,
  ok,
  recordingFetch,
  settle,
} from './helpers/module-dom.mjs';

const privateEntry = (name) =>
  import(pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, `${name}.js`)));
const [{ useConversation }, { OpenVikingProvider }] = await Promise.all([
  privateEntry('console-conversation'),
  privateEntry('provider'),
]);

test('host-controlled directory selection shares the built-in picker and submitted draft', async () => {
  const directory = {
    uri: 'viking://resources',
    type: 'directory',
    name: 'Resources',
  };
  const file = {
    uri: `${directory.uri}/report.md`,
    type: 'file',
    name: 'report.md',
  };
  const searched = deferred();
  const sent = deferred();
  const fetch = recordingFetch(({ path }) => {
    if (path === '/api/v1/fs/ls')
      return ok([{ uri: directory.uri, isDir: true }]);
    assert.equal(path, '/api/v1/search/glob');
    searched.resolve();
    return ok({ matches: [file.uri] });
  });
  const service = {
    createSession: async () => ({ id: 'session', title: 'Session' }),
    loadMessages: async () => [],
    listSessions: async () => [],
    removeSession: async () => {},
    async *stream(input) {
      sent.resolve(input);
      yield { type: 'complete', text: 'Ready' };
    },
  };
  let view, selectReferences;
  function ConsoleProbe() {
    const [selected, onChange] = useState([]);
    const [draft, setDraft] = useState('');
    selectReferences = onChange;
    view = useConversation(
      {
        chatOptions: {
          service,
          referenceSelection: { selected, onChange },
        },
      },
      setDraft,
      draft,
    );
    return null;
  }
  const host = await mount(
    h(
      OpenVikingProvider,
      { fetch, baseURL: 'https://ov.test' },
      h(ConsoleProbe),
    ),
  );
  try {
    await act(() => selectReferences([directory]));
    assert.deepEqual(view.references.selected, [directory]);
    assert.equal(fetch.calls.length, 0);

    await act(() => view.references.onOpenChange(true));
    await settle();
    assert.ok(view.references.rows.some((row) => row.uri === directory.uri));
    await act(() => view.references.onQueryChange('report'));
    await act(() => searched.promise);
    assert.ok(view.references.rows.some((row) => row.uri === file.uri));
    await act(() => view.references.onChange([directory, file]));

    await act(() =>
      view.onSend({
        text: 'Summarize',
        attachments: [],
        references: view.references.selected,
      }),
    );
    let input;
    await act(async () => {
      input = await sent.promise;
    });
    assert.deepEqual(input.references, [directory, file]);
    assert.deepEqual(view.references.selected, [directory, file]);
    await act(() => selectReferences([]));
    assert.deepEqual(view.references.selected, []);
    assert.ok(view.references.rows.some((row) => row.uri === file.uri));
  } finally {
    await host.unmount();
  }
});
