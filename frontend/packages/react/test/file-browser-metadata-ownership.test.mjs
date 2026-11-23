import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from 'react';
import { deferred, ok, settle } from './helpers/module-dom.mjs';
import { metadataFixture } from './helpers/content-metadata-fixture.mjs';

const uri = 'viking://user/alice/resources/handbook/policy.md';
const body = '# Policy\n\nTwo approvals.\n';
const footer = '<!-- CONFLICTS_CONTENT\n\n-->';

test('ordinary browsing stays independent of Compile; an editor discovers newly added metadata before saving', async () => {
  const f = await metadataFixture({ uri, content: body, kinds: [] });
  try {
    assert.equal(f.tab.source.content, body);
    assert.equal(f.fetch.countOf('/api/v1/compile/configs'), 0);
    f.setContent(body + footer);
    assert.equal(await f.tab.editing.loadContent(), body);
    await f.tab.editing.save(body + 'Reviewed.\n');
    const write = f.fetch.calls.find((call) => call.body);
    assert.equal(write.body.resolve_conflicts, true);
    assert.doesNotMatch(write.body.content, /CONFLICTS_CONTENT/);
  } finally {
    await f.view.unmount();
  }
});

test('an ownership lookup failure cannot publish reserved metadata as body text', async () => {
  const registry = Promise.resolve(
    new Response(
      JSON.stringify({
        status: 'error',
        error: { code: 'UNAVAILABLE', message: 'Compile is unavailable' },
      }),
      { status: 503, headers: { 'content-type': 'application/json' } },
    ),
  );
  const f = await metadataFixture({ uri, content: body + footer, registry });
  try {
    assert.match(f.browser.previewError.message, /Compile is unavailable/);
    assert.ok(
      f.history.every((value) => !value?.includes('CONFLICTS_CONTENT')),
    );
  } finally {
    await f.view.unmount();
  }
});

test('another user’s tagged target establishes Wiki editing without a configuration lookup', async () => {
  const f = await metadataFixture({
    uri,
    content: body + footer,
    kinds: [],
    taggedTarget: 'viking://user/alice/resources/handbook',
    registry: Promise.resolve(
      new Response('Configuration listing unavailable', { status: 503 }),
    ),
  });
  try {
    assert.equal(f.tab.source.content, body);
    assert.equal(await f.tab.editing.loadContent(), body);
    await f.tab.editing.save(body + 'Reviewed.\n');
    const write = f.fetch.calls.find((call) => call.body).body;
    assert.equal(write.use_snapshot, true);
    assert.equal(write.resolve_conflicts, true);
    assert.equal('edit_source' in write, false);
    assert.equal(f.fetch.countOf('/api/v1/compile/configs'), 0);
  } finally {
    await f.view.unmount();
  }
});

test('background Compile ownership leaves long manual drafts on the ordinary save protocol', async () => {
  const registry = deferred();
  const content = 'manual\n'.repeat(2001);
  const f = await metadataFixture({
    uri,
    content,
    kinds: [],
    registry: registry.promise,
  });
  try {
    assert.equal(f.tab.source.truncated, true);
    const draft = f.tab.editing;
    await act(async () =>
      registry.resolve(
        await ok({
          targets: [{ to: 'viking://user/alice/resources/handbook' }],
        }),
      ),
    );
    await settle();
    assert.equal(f.tab.editing, draft);
    await f.tab.editing.save(content);
    const write = f.fetch.calls.find((call) => call.path.endsWith('/write'));
    assert.deepEqual(write.body, { uri, content, mode: 'replace' });
  } finally {
    await f.view.unmount();
  }
});

test('a known Wiki keeps the truncation hint when its full read fails', async () => {
  const full = deferred();
  const content = 'line\n'.repeat(2001);
  const f = await metadataFixture({
    uri,
    content,
    taggedTarget: uri,
    fullRead: full.promise,
  });
  try {
    assert.equal(f.tab.source.truncated, true);
    assert.equal(
      f.fetch.calls.filter((call) => call.query.limit === '-1').length,
      1,
    );
    await act(async () => full.resolve(new Response(null, { status: 503 })));
    await settle();
    assert.equal(f.tab.source.truncated, true);
    assert.ok(f.browser.previewError);
  } finally {
    await f.view.unmount();
  }
});

test('a Compile namespace Wiki keeps its label when Dashboard attrs fail', async () => {
  const f = await metadataFixture({
    uri: 'viking://system/compile/global/chapter.md',
    content: body + footer,
    attrs: Promise.resolve(new Response(null, { status: 503 })),
  });
  try {
    assert.equal(f.fetch.countOf('/api/v1/fs/attrs'), 1);
    assert.equal(f.fetch.countOf('/api/v1/compile/configs'), 0);
    assert.equal(f.tab.source.content, body);
    assert.equal(f.browser.previewIsCompile, true);
  } finally {
    await f.view.unmount();
  }
});

test('Compile ownership recognizes the server-normalized home URI', async () => {
  const f = await metadataFixture({
    uri: 'viking://~//compile/chapter.md',
    canonicalUri: 'viking://user/alice/compile/chapter.md',
    content: body + footer,
    registry: ok({ targets: [] }),
  });
  try {
    assert.equal(f.tab.source.content, body);
    assert.equal(f.browser.previewIsCompile, true);
    assert.equal(f.fetch.countOf('/api/v1/compile/configs'), 0);
  } finally {
    await f.view.unmount();
  }
});
