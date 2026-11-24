import assert from 'node:assert/strict';
import test from 'node:test';
import { ok, recordingFetch } from './helpers/module-dom.mjs';
import {
  card,
  click,
  mountHitl,
  settle,
  submit,
} from './helpers/console-hitl.mjs';

const source = 'viking://resources/source';
const target = 'viking://resources/notes_wiki';
const skill = 'viking://agent/skills/llm-wiki';
const cards = [
  card('compile_target', 'Target', { suggested_value: target }),
  card('compile_name', 'Name', { suggested_value: 'notes' }),
  card('compile_skill', 'Skill', {
    candidates: [{ uri: skill, label: 'Wiki' }],
  }),
  card('compile_source', 'Source', { suggested_values: [source] }),
  card('compile_instruction', 'Instructions', {
    current_value: 'Keep citations.',
  }),
  card('compile_schedule', 'Schedule'),
];

test('complete Wiki answers are reviewed, validated and sent once in tool order', async () => {
  const fetch = recordingFetch(({ uri }) => ok({ uri, isDir: true }));
  const view = await mountHitl(cards, { fetch, locale: 'en-US' });
  try {
    await view.start();
    assert.match(
      view.container.querySelector('h3').textContent,
      /Review the Wiki configuration/,
    );
    assert.match(
      view.container.querySelector('dl').textContent,
      /Keep citations./,
    );
    assert.equal(fetch.countOf('/api/v1/fs/stat'), 0);
    assert.equal(view.requests.length, 1);
    await submit(view);
    await settle();
    assert.deepEqual(
      fetch.calls
        .filter(({ path }) => path.endsWith('/fs/stat'))
        .map(({ uri }) => uri)
        .sort(),
      [source, target, skill].sort(),
    );
    assert.equal(view.requests.length, 2);
    assert.deepEqual(
      JSON.parse(view.requests[1].text).answers.map(({ value }) => value),
      [
        target,
        'notes',
        skill,
        [source],
        'Keep citations.',
        { frequency: 'disabled' },
      ],
    );
  } finally {
    await view.unmount();
  }
});

test('failed Wiki review validation preserves the configuration for retry or edit', async () => {
  let readOnly = true;
  const fetch = recordingFetch(({ uri }) =>
    ok({
      uri,
      isDir: true,
      access: uri === target && readOnly ? 'read' : 'manage',
    }),
  );
  const view = await mountHitl(cards, { fetch, locale: 'en-US' });
  try {
    await view.start();
    await submit(view);
    await settle();
    assert.equal(view.requests.length, 1);
    assert.match(
      view.container.querySelector('dl').textContent,
      /Keep citations./,
    );
    assert.match(document.body.textContent, /This directory is not writable/);
    readOnly = false;
    await settle();
    const edit = [...view.container.querySelectorAll('button')].find(
      (node) => node.textContent === 'Edit',
    );
    await click(edit);
    assert.equal(view.container.querySelector('dl') === null, true);
    assert.equal(view.container.querySelector('[name=answer]').value, 'notes');
    assert.equal(
      view.container.querySelector('button[type=submit]').textContent,
      'Next',
    );
    assert.equal(view.requests.length, 1);
  } finally {
    await view.unmount();
  }
});
