import assert from 'node:assert/strict';
import test from 'node:test';
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
