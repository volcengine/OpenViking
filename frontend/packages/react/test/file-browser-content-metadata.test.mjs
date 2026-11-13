import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from 'react';
import { metadataFixture } from './helpers/content-metadata-fixture.mjs';
import { deferred, ok, settle } from './helpers/module-dom.mjs';

const root = 'viking://user/alice';
const wiki = `${root}/resources/handbook`;
const file = `${wiki}/policy.md`;
const head = '---\ntype: wiki\ntitle: Policy\n---\n';
const body = `${head}# Policy\n\ntwo approvals\n`;
const record = {
  match_text: 'two approvals',
  problem_description: 'Approval policy differs',
  reason: 'Two sources disagree',
  severity: 'high',
  state: 'unfixed',
  begin_time: 100,
  fix_time: null,
  fix_plan: 'Verify policy',
  resolution_basis: '',
};
const footer = `<!-- CONFLICTS_CONTENT\n${JSON.stringify(record)}\n-->`;

const fixture = (options = {}) =>
  metadataFixture({
    uri: file,
    content: body + footer,
    ...options,
  });

for (const kinds of [undefined, []]) {
  test(`Wiki preview and draft omit metadata with ${kinds ? 'hidden' : 'visible'} dashboards`, async () => {
    const f = await fixture({
      kinds,
      uri: 'viking://~/resources/handbook/policy.md',
    });
    try {
      assert.equal(f.tab.source.content, body);
      assert.equal(await f.tab.editing.loadContent(), body);
      assert.equal(f.tab.editing.createVersion, undefined);
      assert.ok(
        f.history.every((value) => !value?.includes('CONFLICTS_CONTENT')),
      );
      await f.tab.editing.save(`${body}Updated.\n`);
      assert.deepEqual(
        f.fetch.calls.find((call) => call.path.endsWith('/write')).body,
        {
          uri: 'viking://~/resources/handbook/policy.md',
          content: `${body}Updated.\n`,
          mode: 'replace',
          edit_source: 'manual',
          resolve_conflicts: true,
        },
      );
      if (!kinds) {
        await act(async () =>
          f.browser.actions.openDashboard(f.browser.currentUri),
        );
        await settle();
        assert.match(f.view.container.textContent, /Approval policy differs/);
      }
      assert.ok(
        f.fetch.calls
          .filter((call) => call.path.endsWith('/read'))
          .every((call) => call.query.raw === 'false'),
      );
    } finally {
      await f.view.unmount();
    }
  });
}

for (const uri of [file, `${wiki}/skills/example.md`]) {
  test(`Wiki classification completes before raw content can enter ${uri}`, async () => {
    const registry = deferred();
    const f = await fixture({ uri, registry: registry.promise });
    try {
      assert.equal(f.fetch.countOf('/api/v1/content/read'), 1);
      assert.ok(
        f.history.every((value) => !value?.includes('CONFLICTS_CONTENT')),
      );
      await act(async () =>
        registry.resolve(await ok({ targets: [{ to: wiki }] })),
      );
      await settle();
      assert.equal(f.tab.source.content, body);
      assert.ok(
        f.history.every((value) => !value?.includes('CONFLICTS_CONTENT')),
      );
    } finally {
      await f.view.unmount();
    }
  });
}

for (const suffix of [
  footer,
  '<!-- CONFLICTS_CONTENT {broken -->',
  '<!-- CONFLICTS_CONTENT {broken',
]) {
  test(`metadata is removed before the preview cap, including ${suffix === footer ? 'valid' : 'invalid'} payloads`, async () => {
    const text = 'line\n'.repeat(1998);
    const f = await fixture({ content: `${text}${suffix}` });
    try {
      assert.equal(f.tab.source.content, text);
      assert.equal(f.tab.source.truncated, false);
      assert.equal(await f.tab.editing.loadContent(), text);
      if (suffix === footer)
        assert.ok(f.fetch.calls.some((call) => call.query.limit === '-1'));
      else {
        await act(async () => f.browser.actions.openDashboard(file));
        await settle();
        assert.match(
          f.view.container.textContent,
          /Invalid CONFLICTS_CONTENT metadata/,
        );
      }
    } finally {
      await f.view.unmount();
    }
  });
}

test('consecutive Wiki trailers remain available to the conflict dashboard', async () => {
  const f = await fixture({
    content: `${body}<!-- CONFLICTS_CONTENT -->\n${footer}\n${footer}`,
  });
  try {
    assert.equal(f.tab.source.content, body);
    await act(async () => f.browser.actions.openDashboard(file));
    await settle();
    assert.equal(
      f.view.container.querySelectorAll('.ov-wiki-dashboard__conflict').length,
      2,
    );
  } finally {
    await f.view.unmount();
  }
});

for (const uri of [
  `${root}/skills/report/SKILL.md`,
  `${root}/resources/notes.md`,
  `${wiki}/data.json`,
]) {
  test(`ordinary comments and YAML remain editable at ${uri}`, async () => {
    const text = uri.endsWith('.json')
      ? JSON.stringify({ example: footer })
      : `---\nname: report\ndescription: Keep me\n---\n<!-- ordinary -->\n${footer}`;
    const f = await fixture({ uri, content: text });
    try {
      assert.equal(f.tab.source.content, text);
      assert.equal(await f.tab.editing.loadContent(), text);
      await f.tab.editing.save(text);
      assert.equal(
        'resolve_conflicts' in f.fetch.calls.find((call) => call.body).body,
        false,
      );
    } finally {
      await f.view.unmount();
    }
  });
}

for (const uri of [`${root}/memories/rules/policy.md`, file]) {
  test(`quoted markers survive while an incomplete real trailer is hidden at ${uri}`, async () => {
    const memory = uri.includes('/memories/');
    const marker = memory ? 'MEMORY_FIELDS' : 'CONFLICTS_CONTENT';
    const example = `${head}\`\`\`html\n<!-- ${marker}\n\`\`\`\n\nExamples above.\n`;
    const f = await fixture({
      uri,
      content: `${example}<!-- ${marker} {broken`,
    });
    try {
      assert.equal(f.tab.source.content, memory ? example.trimEnd() : example);
      assert.equal(await f.tab.editing.loadContent(), f.tab.source.content);
    } finally {
      await f.view.unmount();
    }
  });
}
